"""Benchmark the embedding model on this machine (estimates backfill time).

Usage (from backend/):
    uv run python scripts/bench_embed.py
    uv run python scripts/bench_embed.py --n 200 --tokens 600 --batch-size 8

Passages are consecutive chunks (`embed_text`) of the chunker test fixture joined to about
`--tokens` tokens each (real chunks are up to 800), repeated up to `--n`.
Device, threads and batch size default to `.env` (`EMBEDDING_*`).
"""

# ruff: noqa: E402  (imports must follow the sys.path setup below)
import sys
from pathlib import Path

# Run as `python scripts/bench_embed.py`: make `app` importable from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Must be imported first: exports PYTORCH_ENABLE_MPS_FALLBACK / HF_HOME before torch loads.
from app.core.config import BACKEND_DIR, settings  # isort: skip

import argparse
import itertools
import logging
import platform
import time

from app.ingest.chunker import TokenCounter, bge_token_counter, chunk_document
from app.ingest.embedder import BgeM3Embedder

FIXTURE = BACKEND_DIR / "tests" / "fixtures" / "chunker" / "luat_mau.txt"


def sample_passages(n: int, tokens: int, count: TokenCounter) -> list[str]:
    chunks = chunk_document(
        FIXTURE.read_text(encoding="utf-8"), title="Luật mẫu", so_hieu="01/2024/QH15"
    )
    passages: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for chunk in itertools.cycle(chunks):
        current.append(chunk.embed_text)
        current_tokens += count(chunk.embed_text)  # approx.: ignores the joining newlines
        if current_tokens >= tokens:
            passages.append("\n\n".join(current))
            current = []
            current_tokens = 0
            if len(passages) == n:
                return passages
    raise AssertionError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=100, help="number of passages")
    parser.add_argument("--tokens", type=int, default=400, help="approx. tokens per passage")
    parser.add_argument("--batch-size", type=int, default=settings.embedding_batch_size)
    parser.add_argument("--device", default=settings.embedding_device)
    parser.add_argument("--threads", type=int, default=settings.embedding_threads)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import torch

    count = bge_token_counter()
    passages = sample_passages(args.n, args.tokens, count)
    tokens = sum(count(p) for p in passages)

    started = time.perf_counter()
    emb = BgeM3Embedder(
        settings.embedding_model,
        device=args.device,
        batch_size=args.batch_size,
        threads=args.threads,
    )
    load_s = time.perf_counter() - started

    emb.embed_documents(passages[: args.batch_size])  # warm-up (first batch compiles kernels)
    started = time.perf_counter()
    emb.embed_documents(passages)
    encode_s = time.perf_counter() - started

    print(f"os           {platform.system()} {platform.machine()}")
    print(f"torch        {torch.__version__}")
    print(f"model        {settings.embedding_model}")
    print(f"device       {emb.device}")
    print(f"threads      {torch.get_num_threads()}")
    print(f"batch size   {args.batch_size}")
    print(f"passages     {len(passages)} ({tokens / len(passages):.0f} tokens avg)")
    print(f"load         {load_s:.1f}s")
    print(f"encode       {encode_s:.1f}s ({len(passages) / encode_s:.2f} passages/s)")


if __name__ == "__main__":
    main()
