"""Retrieval eval (T25): recall@k by (số hiệu, Điều), correct refusals, `RAG_MIN_SCORE` sweep.

Usage (from backend/, Postgres + Qdrant running, documents indexed with the real embedder):
    uv run python scripts/eval_retrieval.py
    uv run python scripts/eval_retrieval.py --questions eval/questions.yaml --report eval/report.md

Each question is retrieved once with the threshold disabled (same filters as the chat:
public vbpl documents, expired ones excluded); every threshold is then evaluated offline.
Prints the sweep table and, with --report, writes the Markdown report. Calls no LLM.
"""

# ruff: noqa: E402  (imports must follow the sys.path setup below)
import sys
from pathlib import Path

# Run as `python scripts/eval_retrieval.py`: make `app` importable from backend/.
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import argparse
import asyncio
import time
import uuid
from datetime import datetime

from qdrant_client import AsyncQdrantClient

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.ingest.embedder import get_embedder
from app.rag.evaluation import (
    DEFAULT_KS,
    DEFAULT_THRESHOLDS,
    Observation,
    Question,
    detail_table,
    load_questions,
    recommend,
    score_summary,
    sweep,
    sweep_table,
)
from app.rag.retriever import RetrievalFilters, retrieve

# Below any cosine score: the retriever never refuses, the sweep decides offline.
NO_THRESHOLD = -1.0


async def observe(questions: list[Question], top_k: int) -> list[Observation]:
    embedder = get_embedder()
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    # A user who owns no upload: only public (vbpl) documents are visible.
    filters = RetrievalFilters(user_id=uuid.uuid4())
    observations = []
    try:
        for question in questions:
            async with SessionLocal() as session:
                result = await retrieve(
                    session,
                    qdrant,
                    question.question,
                    filters,
                    top_k,
                    min_score=NO_THRESHOLD,
                    embedder=embedder,
                )
            ranked = tuple((b.document.so_hieu_norm, b.dieu) for b in result.blocks)
            observations.append(Observation(question, result.best_dense_score, ranked))
            print(f"  {question.id:8} best={result.best_dense_score or 0:.3f}", flush=True)
    finally:
        await qdrant.close()
        await engine.dispose()
    return observations


def render_report(observations: list[Observation], top_k: int, elapsed: float) -> str:
    rows = sweep(observations)
    best = recommend(rows)
    summary = score_summary(observations)
    in_scope = sum(o.question.in_scope for o in observations)

    def fmt(value: float | None) -> str:
        return "—" if value is None else f"{value:.3f}"

    return f"""# Báo cáo eval retrieval (T25)

Sinh bởi `scripts/eval_retrieval.py` lúc {datetime.now():%Y-%m-%d %H:%M}.
Embedding `{settings.embedding_model}` ({settings.embedding_backend}), collection
`{settings.qdrant_collection}`, `RAG_TOP_K={top_k}`. {in_scope} câu trong phạm vi,
{len(observations) - in_scope} câu ngoài phạm vi; chạy {elapsed:.0f}s.

Cách đo: mỗi câu gọi `retrieve` một lần với ngưỡng tắt, sau đó áp từng ngưỡng.
Câu trong phạm vi "trúng @k" khi được trả lời (điểm dense tốt nhất ≥ ngưỡng) **và** một
(số hiệu, Điều) kỳ vọng nằm trong k block nguồn đầu tiên. Câu ngoài phạm vi "từ chối đúng"
khi điểm dense tốt nhất < ngưỡng.

## Phân bố điểm dense tốt nhất

- Câu trong phạm vi: thấp nhất {fmt(summary["in_scope_min"])}, trung vị
  {fmt(summary["in_scope_median"])}.
- Câu ngoài phạm vi: cao nhất {fmt(summary["out_of_scope_max"])}.

## Quét `RAG_MIN_SCORE`

{sweep_table(rows)}

**Ngưỡng tốt nhất theo balanced accuracy: `{best.threshold:.2f}`** (trả lời
{best.answered}/{best.in_scope_total} câu trong phạm vi, từ chối đúng
{best.refused}/{best.out_of_scope_total} câu ngoài phạm vi, recall@{DEFAULT_KS[-1]}
{best.recall(DEFAULT_KS[-1]) * 100:.0f}%).

## Chi tiết từng câu

{detail_table(observations)}
"""


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--questions", type=Path, default=BACKEND / "eval" / "questions.yaml")
    parser.add_argument("--report", type=Path, help="write the Markdown report to this file")
    parser.add_argument("--top-k", type=int, default=settings.rag_top_k)
    args = parser.parse_args()
    if args.top_k < max(DEFAULT_KS):
        parser.error(f"--top-k must be >= {max(DEFAULT_KS)} to compute recall@{max(DEFAULT_KS)}")

    questions = load_questions(args.questions)
    low, high = DEFAULT_THRESHOLDS[0], DEFAULT_THRESHOLDS[-1]
    print(f"{len(questions)} questions, thresholds {low}..{high}")
    started = time.perf_counter()
    observations = await observe(questions, args.top_k)
    elapsed = time.perf_counter() - started

    rows = sweep(observations)
    print()
    print(sweep_table(rows))
    print(f"\nRecommended RAG_MIN_SCORE: {recommend(rows).threshold:.2f}")
    if args.report:
        args.report.write_text(render_report(observations, args.top_k, elapsed), encoding="utf-8")
        print(f"Report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
