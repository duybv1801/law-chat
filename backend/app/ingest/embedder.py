"""Dense + sparse embeddings for chunks and queries.

Two implementations share the `Embedder` interface, selected by `EMBEDDING_BACKEND`:
- `BgeM3Embedder`: `BAAI/bge-m3` via FlagEmbedding, running locally (cuda → mps → cpu).
- `FakeEmbedder`: deterministic hashed bag-of-words vectors, no torch. For tests and
  UI-only work on a weak machine.

torch / FlagEmbedding are imported inside `BgeM3Embedder` only, so the fake backend never
loads them. Encoding is CPU/GPU-bound: async code must use `aembed_documents` /
`aembed_query`, which run in a worker thread. Encodes never run in parallel: a
`threading.Lock` is held around each model call, so it also covers sync callers, several
event loops and cancelled coroutines (whose worker thread keeps running).
"""

import asyncio
import hashlib
import logging
import math
import platform
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1024
# Sparse weights below this are dropped (smaller Qdrant points, no measurable recall loss).
MIN_SPARSE_WEIGHT = 0.01
# Chunks are ≤ 800 tokens of text plus title/breadcrumb in `embed_text`; FlagEmbedding's
# default of 512 would silently truncate them.
PASSAGE_MAX_TOKENS = 1024
QUERY_MAX_TOKENS = 512

FAKE_MODEL_NAME = "fake"


@dataclass
class EmbeddingResult:
    dense: list[float]  # EMBEDDING_DIM floats, L2-normalized
    sparse: dict[int, float]  # token_id -> weight


class Embedder(ABC):
    model_name: str
    dim: int = EMBEDDING_DIM
    # Documents are encoded (and the lock held) one slice of this size at a time, so a large
    # upload cannot block chat queries for minutes.
    batch_size: int = 16

    def __init__(self) -> None:
        self._encode_lock = threading.Lock()

    @abstractmethod
    def _embed_documents(self, texts: list[str]) -> list[EmbeddingResult]: ...

    @abstractmethod
    def _embed_query(self, text: str) -> EmbeddingResult: ...

    def _embed_slice(self, texts: list[str]) -> list[EmbeddingResult]:
        with self._encode_lock:
            return self._embed_documents(texts)

    def _slices(self, texts: list[str]) -> list[list[str]]:
        return [texts[i : i + self.batch_size] for i in range(0, len(texts), self.batch_size)]

    def embed_documents(self, texts: list[str]) -> list[EmbeddingResult]:
        return [result for part in self._slices(texts) for result in self._embed_slice(part)]

    def embed_query(self, text: str) -> EmbeddingResult:
        with self._encode_lock:
            return self._embed_query(text)

    async def aembed_documents(self, texts: list[str]) -> list[EmbeddingResult]:
        # One worker thread per slice: queries waiting on the lock get in between slices,
        # and a cancelled caller stops after the current slice.
        results: list[EmbeddingResult] = []
        for part in self._slices(texts):
            results.extend(await asyncio.to_thread(self._embed_slice, part))
        return results

    async def aembed_query(self, text: str) -> EmbeddingResult:
        return await asyncio.to_thread(self.embed_query, text)


def resolve_device(requested: str) -> str:
    """`auto` → cuda, then mps, then cpu. An explicit device must be available."""
    # Exports PYTORCH_ENABLE_MPS_FALLBACK before torch loads.
    import app.core.config  # noqa: F401  # isort: skip
    import torch

    available = {
        "cuda": torch.cuda.is_available(),
        "mps": torch.backends.mps.is_available(),
        "cpu": True,
    }
    if requested == "auto":
        return next(device for device, ok in available.items() if ok)
    if not available.get(requested, False):
        raise RuntimeError(f"EMBEDDING_DEVICE={requested} is not available on this machine")
    return requested


def _sparse(weights: dict[str, float]) -> dict[int, float]:
    # FlagEmbedding keys token ids as strings.
    sparse = {int(k): float(w) for k, w in weights.items() if w >= MIN_SPARSE_WEIGHT}
    if not sparse and weights:
        # Very short queries can have every weight below the cut-off: keep the strongest
        # token rather than sending Qdrant an empty sparse vector.
        k, w = max(weights.items(), key=lambda item: item[1])
        sparse = {int(k): float(w)}
    return sparse


class BgeM3Embedder(Embedder):
    def __init__(
        self,
        model_name: str,
        device: str = "auto",
        batch_size: int = 16,
        threads: int | None = None,
    ) -> None:
        super().__init__()
        # Exports PYTORCH_ENABLE_MPS_FALLBACK / HF_HOME before torch loads.
        import app.core.config  # noqa: F401  # isort: skip
        import torch
        from FlagEmbedding import BGEM3FlagModel

        if threads:
            torch.set_num_threads(threads)  # before loading the model
        self.model_name = model_name
        self.device = resolve_device(device)
        self.batch_size = batch_size
        started = time.perf_counter()
        self._model = BGEM3FlagModel(
            model_name,
            use_fp16=self.device == "cuda",  # mps and cpu run in fp32
            devices=self.device,  # one device: no multi-process pool
            batch_size=batch_size,
            passage_max_length=PASSAGE_MAX_TOKENS,
            query_max_length=QUERY_MAX_TOKENS,
        )
        logger.info(
            "Embedder %s loaded: os=%s device=%s threads=%d batch_size=%d load=%.1fs",
            model_name,
            platform.system(),
            self.device,
            torch.get_num_threads(),
            batch_size,
            time.perf_counter() - started,
        )

    def _encode(self, texts: list[str], max_length: int) -> list[EmbeddingResult]:
        if not texts:
            return []
        out = self._model.encode(
            texts,
            batch_size=self.batch_size,
            max_length=max_length,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        return [
            EmbeddingResult(dense=dense.tolist(), sparse=_sparse(weights))
            for dense, weights in zip(out["dense_vecs"], out["lexical_weights"], strict=True)
        ]

    def _embed_documents(self, texts: list[str]) -> list[EmbeddingResult]:
        return self._encode(texts, PASSAGE_MAX_TOKENS)

    def _embed_query(self, text: str) -> EmbeddingResult:
        return self._encode([text], QUERY_MAX_TOKENS)[0]


_WORD = re.compile(r"\w+")
_FAKE_VOCAB = 250_002  # bge-m3 vocabulary size, so fake sparse ids look like real ones
_FAKE_DIMS_PER_WORD = 8


def _digest(value: str) -> bytes:
    # hashlib, not hash(): str hashes are salted per process.
    return hashlib.blake2b(value.encode(), digest_size=32).digest()


class FakeEmbedder(Embedder):
    """Deterministic hashed bag-of-words: texts sharing words get similar vectors, which is
    enough for retrieval tests. Not a language model."""

    model_name = FAKE_MODEL_NAME

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        super().__init__()
        self.dim = dim

    def _embed(self, text: str) -> EmbeddingResult:
        words = _WORD.findall(text.lower()) or [text]  # no words: hash the raw text
        dense = [0.0] * self.dim
        counts: dict[int, int] = {}
        for word in words:
            digest = _digest(word)
            for i in range(_FAKE_DIMS_PER_WORD):
                index = int.from_bytes(digest[i * 3 : i * 3 + 3]) % self.dim
                dense[index] += 1.0 if digest[24 + i] & 1 else -1.0
            token_id = int.from_bytes(digest[:4]) % _FAKE_VOCAB
            counts[token_id] = counts.get(token_id, 0) + 1
        norm = math.sqrt(sum(x * x for x in dense))
        if norm == 0:  # signs cancelled out exactly
            dense[int.from_bytes(_digest(text)[:4]) % self.dim] = 1.0
            norm = 1.0
        top = max(counts.values())
        return EmbeddingResult(
            dense=[x / norm for x in dense],
            sparse={token_id: n / top for token_id, n in counts.items()},
        )

    def _embed_documents(self, texts: list[str]) -> list[EmbeddingResult]:
        return [self._embed(text) for text in texts]

    def _embed_query(self, text: str) -> EmbeddingResult:
        return self._embed(text)


def create_embedder() -> Embedder:
    """A new embedder from settings. Loading bge-m3 takes seconds and ~3 GB of RAM (and
    downloads ~2.3 GB once): use `get_embedder()` instead of calling this repeatedly."""
    from app.core.config import settings

    if settings.embedding_backend == "fake":
        return FakeEmbedder()
    return BgeM3Embedder(
        settings.embedding_model,
        device=settings.embedding_device,
        batch_size=settings.embedding_batch_size,
        threads=settings.embedding_threads,
    )


_embedder: Embedder | None = None
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    """Process-wide singleton. The API loads it in the lifespan; CLIs on first use.
    Thread-safe: concurrent first calls load the model once."""
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                _embedder = create_embedder()
    return _embedder
