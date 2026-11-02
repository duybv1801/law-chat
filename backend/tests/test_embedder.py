import asyncio
import math
import os
import subprocess
import sys
import threading
import time

import pytest

from app.core.config import BACKEND_DIR
from app.ingest.embedder import (
    EMBEDDING_DIM,
    MIN_SPARSE_WEIGHT,
    EmbeddingResult,
    FakeEmbedder,
    _sparse,
    create_embedder,
    get_embedder,
)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))  # inputs are normalized


def test_fake_is_deterministic_and_normalized():
    a = FakeEmbedder().embed_query("Thời hạn nộp thuế thu nhập cá nhân")
    b = FakeEmbedder().embed_query("Thời hạn nộp thuế thu nhập cá nhân")
    assert a == b
    assert len(a.dense) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(x * x for x in a.dense)), 1.0)
    assert a.sparse and all(isinstance(k, int) and 0 < w <= 1 for k, w in a.sparse.items())


def test_fake_word_overlap_scores_higher():
    emb = FakeEmbedder()
    query = emb.embed_query("mức phạt vi phạm giao thông")
    related, unrelated = emb.embed_documents(
        ["Mức phạt tiền đối với vi phạm giao thông đường bộ", "Thủ tục đăng ký kết hôn"]
    )
    assert cosine(query.dense, related.dense) > cosine(query.dense, unrelated.dense)
    assert set(query.sparse) & set(related.sparse)


def test_fake_edge_cases():
    emb = FakeEmbedder()
    assert emb.embed_documents([]) == []
    for text in ["", "...", "a a"]:
        result = emb.embed_query(text)
        assert math.isclose(math.sqrt(sum(x * x for x in result.dense)), 1.0)
    # Same words in a different order: bag of words gives the same vector.
    assert emb.embed_query("điều một").dense == emb.embed_query("một điều").dense


def test_fake_documents_match_query():
    emb = FakeEmbedder()
    texts = ["Điều 1. Phạm vi điều chỉnh", "Điều 2. Đối tượng áp dụng"]
    assert emb.embed_documents(texts) == [emb.embed_query(t) for t in texts]


def test_sparse_conversion():
    assert _sparse({"5": 0.3, "17": 0.005, "9": MIN_SPARSE_WEIGHT}) == {5: 0.3, 9: 0.01}


def test_sparse_keeps_strongest_token_when_all_below_cutoff():
    assert _sparse({"5": 0.002, "17": 0.005}) == {17: 0.005}
    assert _sparse({}) == {}


def test_factory_uses_fake_backend():
    # conftest sets EMBEDDING_BACKEND=fake
    assert isinstance(create_embedder(), FakeEmbedder)
    assert get_embedder() is get_embedder()
    assert get_embedder().model_name == "fake"


def test_fake_backend_does_not_import_torch():
    code = (
        "import sys\n"
        "from app.ingest.embedder import get_embedder\n"
        "get_embedder().embed_documents(['Điều 1'])\n"
        "import app.main\n"
        "bad = [m for m in ('torch', 'FlagEmbedding', 'transformers') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    env = {**os.environ, "EMBEDDING_BACKEND": "fake"}
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND_DIR, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


class _SlowFake(FakeEmbedder):
    def __init__(self, delay: float = 0.02) -> None:
        super().__init__()
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.threads: set[int] = set()
        self.calls: list[list[str]] = []
        self._guard = threading.Lock()

    def _track(self, texts: list[str]) -> None:
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(texts)
        self.threads.add(threading.get_ident())
        time.sleep(self.delay)
        with self._guard:
            self.active -= 1

    def _embed_documents(self, texts: list[str]) -> list[EmbeddingResult]:
        self._track(texts)
        return super()._embed_documents(texts)

    def _embed_query(self, text: str) -> EmbeddingResult:
        self._track([text])
        return super()._embed_query(text)


async def test_async_wrappers_never_encode_in_parallel():
    emb = _SlowFake()
    results = await asyncio.gather(*(emb.aembed_documents([f"văn bản {i}"]) for i in range(5)))
    assert await emb.aembed_query("điều 1") == FakeEmbedder().embed_query("điều 1")
    assert emb.max_active == 1
    assert threading.get_ident() not in emb.threads  # ran off the event loop thread
    assert [r[0] for r in results] == [emb.embed_query(f"văn bản {i}") for i in range(5)]


async def test_sync_and_async_callers_share_the_lock():
    emb = _SlowFake()
    sync_calls = [asyncio.to_thread(emb.embed_query, f"điều {i}") for i in range(3)]
    async_calls = [emb.aembed_query(f"khoản {i}") for i in range(3)]
    await asyncio.gather(*sync_calls, *async_calls)
    assert emb.max_active == 1


async def test_cancelled_caller_does_not_release_the_model():
    emb = _SlowFake(delay=0.2)
    task = asyncio.create_task(emb.aembed_query("điều 1"))
    await asyncio.sleep(0.05)  # the worker thread is now encoding
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await emb.aembed_query("điều 2")  # must wait for the orphaned encode to finish
    assert emb.max_active == 1


async def test_documents_are_encoded_in_slices_so_queries_get_in():
    emb = _SlowFake()
    emb.batch_size = 2
    texts = [f"văn bản {i}" for i in range(6)]
    docs = asyncio.create_task(emb.aembed_documents(texts))
    await asyncio.sleep(0.01)  # first slice is encoding
    query = await emb.aembed_query("câu hỏi")
    assert not docs.done()  # the query did not wait for the whole upload
    assert await docs == FakeEmbedder().embed_documents(texts)
    assert ["câu hỏi"] in emb.calls
    assert all(len(call) <= 2 for call in emb.calls)
    assert query == FakeEmbedder().embed_query("câu hỏi")
    assert emb.embed_documents(texts) == FakeEmbedder().embed_documents(texts)


def test_get_embedder_loads_once_under_concurrency(monkeypatch):
    from app.ingest import embedder as module

    created: list[object] = []

    def slow_create():
        time.sleep(0.05)
        created.append(object())
        return FakeEmbedder()

    monkeypatch.setattr(module, "_embedder", None)
    monkeypatch.setattr(module, "create_embedder", slow_create)
    threads = [threading.Thread(target=module.get_embedder) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(created) == 1


@pytest.mark.slow
def test_bge_m3_real_model():
    from app.core.config import settings
    from app.ingest.embedder import BgeM3Embedder

    emb = BgeM3Embedder(
        settings.embedding_model,
        device=settings.embedding_device,
        batch_size=settings.embedding_batch_size,
        threads=settings.embedding_threads,
    )
    assert emb.device in {"cuda", "mps", "cpu"}
    texts = [
        "Người lao động được nghỉ việc hưởng nguyên lương 5 ngày khi kết hôn.",
        "Khi lập gia đình, người lao động có quyền nghỉ 5 ngày và vẫn nhận đủ tiền lương.",
        "Xe ô tô chạy quá tốc độ bị phạt tiền từ 800.000 đồng.",
        "Điều " + "1. Phạm vi điều chỉnh " * 400,  # longer than the model input limit
    ]
    married, synonym, unrelated, long = emb.embed_documents(texts)
    for r in (married, synonym, unrelated, long):
        assert len(r.dense) == EMBEDDING_DIM
        assert math.isclose(math.sqrt(sum(x * x for x in r.dense)), 1.0, rel_tol=1e-3)
        assert r.sparse and all(isinstance(k, int) for k in r.sparse)
        assert min(r.sparse.values()) >= MIN_SPARSE_WEIGHT
    assert cosine(married.dense, synonym.dense) > cosine(married.dense, unrelated.dense) + 0.1

    # Output order matches input order (FlagEmbedding sorts by length internally).
    query = emb.embed_query(texts[2])
    assert cosine(query.dense, unrelated.dense) > 0.99
    assert emb.embed_documents([]) == []
