"""Retrieval eval (T25): question set, metrics and the `RAG_MIN_SCORE` sweep.

Pure logic, no database: `scripts/eval_retrieval.py` runs the retriever once per question
with the threshold disabled and records what came back (`Observation`); every threshold is
then evaluated offline, since the threshold only decides whether the result is used.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.ingest.schemas import normalize_so_hieu

DEFAULT_KS = (1, 3, 5, 8)
DEFAULT_THRESHOLDS = tuple(round(0.30 + 0.05 * i, 2) for i in range(9))  # 0.30 .. 0.70


@dataclass(frozen=True)
class Target:
    so_hieu_norm: str
    dieu: str


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    # Empty for an out-of-scope question (expected: refusal).
    expected: tuple[Target, ...] = ()

    @property
    def in_scope(self) -> bool:
        return bool(self.expected)


@dataclass(frozen=True)
class Observation:
    question: Question
    # Best dense cosine score among visible points (None: nothing visible at all).
    best_score: float | None
    # (so_hieu_norm, dieu) of the returned source blocks, ranked best first.
    ranked: tuple[tuple[str | None, str | None], ...]

    def rank(self) -> int | None:
        """1-based rank of the first expected Điều among the blocks, None if missing."""
        wanted = {(t.so_hieu_norm, t.dieu) for t in self.question.expected}
        for position, key in enumerate(self.ranked, start=1):
            if key in wanted:
                return position
        return None

    def answered(self, threshold: float) -> bool:
        return self.best_score is not None and self.best_score >= threshold


@dataclass(frozen=True)
class ThresholdRow:
    threshold: float
    in_scope_total: int
    # In-scope questions that pass the threshold (not refused).
    answered: int
    # k -> in-scope questions answered with an expected Điều in the top k.
    hits: dict[int, int]
    out_of_scope_total: int
    refused: int

    @property
    def answer_rate(self) -> float:
        return _ratio(self.answered, self.in_scope_total)

    def recall(self, k: int) -> float:
        return _ratio(self.hits[k], self.in_scope_total)

    @property
    def refusal_rate(self) -> float:
        return _ratio(self.refused, self.out_of_scope_total)

    @property
    def balanced_accuracy(self) -> float:
        """Mean of "in-scope answered" and "out-of-scope refused"."""
        return (self.answer_rate + self.refusal_rate) / 2


def _ratio(part: int, total: int) -> float:
    return part / total if total else 0.0


class EvalSetError(ValueError):
    pass


def parse_questions(data: object) -> list[Question]:
    """Questions from the parsed YAML (see `eval/questions.yaml`)."""
    if not isinstance(data, dict):
        raise EvalSetError("eval set must be a mapping with in_scope / out_of_scope")
    questions: list[Question] = []
    for section, needs_expected in (("in_scope", True), ("out_of_scope", False)):
        for item in data.get(section) or []:
            if not isinstance(item, dict) or not item.get("id") or not item.get("question"):
                raise EvalSetError(f"{section}: each item needs id and question: {item!r}")
            expected = item.get("expected") or []
            if needs_expected and not expected:
                raise EvalSetError(f"{item['id']}: in-scope question without expected")
            if not needs_expected and expected:
                raise EvalSetError(f"{item['id']}: out-of-scope question with expected")
            targets = []
            for target in expected:
                so_hieu = normalize_so_hieu(str(target.get("so_hieu") or ""))
                dieu = str(target.get("dieu") or "").strip()
                if not so_hieu or not dieu:
                    raise EvalSetError(f"{item['id']}: expected needs so_hieu and dieu")
                targets.append(Target(so_hieu, dieu))
            questions.append(
                Question(str(item["id"]), str(item["question"]).strip(), tuple(targets))
            )
    ids = [q.id for q in questions]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise EvalSetError(f"duplicate ids: {', '.join(duplicates)}")
    return questions


def load_questions(path: Path) -> list[Question]:
    return parse_questions(yaml.safe_load(path.read_text(encoding="utf-8")))


def evaluate(
    observations: Sequence[Observation],
    threshold: float,
    ks: Sequence[int] = DEFAULT_KS,
) -> ThresholdRow:
    in_scope = [o for o in observations if o.question.in_scope]
    out_of_scope = [o for o in observations if not o.question.in_scope]
    answered = [o for o in in_scope if o.answered(threshold)]
    hits = {k: 0 for k in ks}
    for observation in answered:
        rank = observation.rank()
        for k in ks:
            if rank is not None and rank <= k:
                hits[k] += 1
    return ThresholdRow(
        threshold=threshold,
        in_scope_total=len(in_scope),
        answered=len(answered),
        hits=hits,
        out_of_scope_total=len(out_of_scope),
        refused=sum(not o.answered(threshold) for o in out_of_scope),
    )


def sweep(
    observations: Sequence[Observation],
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
    ks: Sequence[int] = DEFAULT_KS,
) -> list[ThresholdRow]:
    return [evaluate(observations, t, ks) for t in thresholds]


def recommend(rows: Sequence[ThresholdRow]) -> ThresholdRow:
    """The row with the best balanced accuracy; among equally good consecutive rows, the
    middle one (margin on both sides). Ties between separate plateaus: the lowest one."""
    if not rows:
        raise ValueError("no rows")
    best = max(row.balanced_accuracy for row in rows)
    plateau: list[ThresholdRow] = []
    for row in rows:
        if row.balanced_accuracy == best:
            plateau.append(row)
        elif plateau:
            break  # first plateau only
    return plateau[(len(plateau) - 1) // 2]


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def sweep_table(rows: Sequence[ThresholdRow], ks: Sequence[int] = DEFAULT_KS) -> str:
    header = ["RAG_MIN_SCORE", "Trả lời (trong phạm vi)"]
    header += [f"recall@{k}" for k in ks]
    header += ["Từ chối đúng (ngoài phạm vi)", "Balanced acc."]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        cells = [
            f"{row.threshold:.2f}",
            f"{row.answered}/{row.in_scope_total} ({_pct(row.answer_rate)})",
        ]
        cells += [f"{_pct(row.recall(k))}" for k in ks]
        cells += [
            f"{row.refused}/{row.out_of_scope_total} ({_pct(row.refusal_rate)})",
            _pct(row.balanced_accuracy),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _score(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def detail_table(observations: Sequence[Observation]) -> str:
    lines = [
        "| ID | Câu hỏi | Kỳ vọng | Điểm dense tốt nhất | Hạng Điều kỳ vọng | Top 3 trả về |",
        "|---|---|---|---|---|---|",
    ]
    for o in observations:
        q = o.question
        expected = ", ".join(f"{t.so_hieu_norm} {t.dieu}" for t in q.expected) or "từ chối"
        rank = o.rank() if q.in_scope else None
        rank_text = ("không có" if rank is None else str(rank)) if q.in_scope else "—"
        top = "; ".join(f"{s or '?'} {d or '(không Điều)'}" for s, d in o.ranked[:3]) or "—"
        question = q.question.replace("|", "\\|")
        lines.append(
            f"| {q.id} | {question} | {expected} | {_score(o.best_score)} | {rank_text} | {top} |"
        )
    return "\n".join(lines)


def score_summary(observations: Sequence[Observation]) -> dict[str, float | None]:
    """Score ranges that bound the useful threshold: anything above the best out-of-scope
    score refuses all of them; anything below the worst in-scope score answers all."""
    in_scores = [o.best_score for o in observations if o.question.in_scope]
    out_scores = [o.best_score for o in observations if not o.question.in_scope]
    known_in = [s for s in in_scores if s is not None]
    known_out = [s for s in out_scores if s is not None]
    return {
        "in_scope_min": min(known_in, default=None),
        "in_scope_median": sorted(known_in)[len(known_in) // 2] if known_in else None,
        "out_of_scope_max": max(known_out, default=None),
    }
