"""Retrieval eval logic (T25): question set parsing, metrics, sweep, recommendation."""

from pathlib import Path

import pytest

from app.rag.evaluation import (
    DEFAULT_THRESHOLDS,
    EvalSetError,
    Observation,
    Question,
    Target,
    detail_table,
    evaluate,
    load_questions,
    parse_questions,
    recommend,
    score_summary,
    sweep,
    sweep_table,
)

QUESTIONS_FILE = Path(__file__).resolve().parents[1] / "eval" / "questions.yaml"

A = Target("100/2015/QH13", "Điều 173")
B = Target("92/2015/QH13", "Điều 273")


def q(id_: str, *expected: Target) -> Question:
    return Question(id_, f"câu {id_}", tuple(expected))


def obs(question: Question, score: float | None, *ranked: tuple[str, str]) -> Observation:
    return Observation(question, score, tuple(ranked))


def key(target: Target) -> tuple[str, str]:
    return (target.so_hieu_norm, target.dieu)


# --- parsing ---


def test_bundled_question_set_is_valid():
    questions = load_questions(QUESTIONS_FILE)
    in_scope = [x for x in questions if x.in_scope]
    assert len(in_scope) == 30
    assert len(questions) - len(in_scope) == 5


def test_parse_normalizes_so_hieu_and_accepts_several_targets():
    data = {
        "in_scope": [
            {
                "id": "a",
                "question": " Hỏi? ",
                "expected": [
                    {"so_hieu": "100/2015/qh13", "dieu": "Điều 168"},
                    {"so_hieu": "100/2015/QH13", "dieu": "Điều 171 "},
                ],
            }
        ],
        "out_of_scope": [{"id": "b", "question": "Phở?"}],
    }
    a, b = parse_questions(data)
    assert a.question == "Hỏi?"
    assert a.expected == (
        Target("100/2015/QH13", "Điều 168"),
        Target("100/2015/QH13", "Điều 171"),
    )
    assert a.in_scope and not b.in_scope


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"in_scope": [{"id": "a", "question": "x"}]},  # in scope without expected
        {
            "out_of_scope": [
                {"id": "a", "question": "x", "expected": [{"so_hieu": "1", "dieu": "2"}]}
            ]
        },
        {"in_scope": [{"id": "a", "question": "x", "expected": [{"so_hieu": "1/2015/QH13"}]}]},
        {
            "in_scope": [
                {"question": "x", "expected": [{"so_hieu": "1/2015/QH13", "dieu": "Điều 1"}]}
            ]
        },
        {"out_of_scope": [{"id": "a", "question": "x"}, {"id": "a", "question": "y"}]},
    ],
)
def test_parse_rejects_invalid_sets(data):
    with pytest.raises(EvalSetError):
        parse_questions(data)


# --- metrics ---


def test_rank_is_first_expected_block():
    question = q("a", A, B)
    observation = obs(question, 0.8, ("X", "Điều 1"), key(B), key(A))
    assert observation.rank() == 2
    assert obs(question, 0.8, ("X", "Điều 1")).rank() is None
    # Same Điều of another document is not a hit.
    assert obs(question, 0.8, ("101/2015/QH13", "Điều 173")).rank() is None


def test_answered_respects_threshold_and_missing_score():
    question = q("a", A)
    assert obs(question, 0.6).answered(0.6)
    assert not obs(question, 0.59).answered(0.6)
    assert not obs(question, None).answered(-1.0)


def test_evaluate_counts_hits_only_for_answered_questions():
    observations = [
        obs(q("hit1", A), 0.8, key(A)),
        obs(q("hit3", A), 0.7, ("X", "Điều 1"), ("X", "Điều 2"), key(A)),
        obs(q("miss", A), 0.9, ("X", "Điều 1")),
        obs(q("low", A), 0.5, key(A)),  # right Điều but refused
        obs(q("oos1"), 0.55),
        obs(q("oos2"), 0.3),
    ]
    row = evaluate(observations, 0.6, ks=(1, 3))
    assert (row.in_scope_total, row.answered) == (4, 3)
    assert row.hits == {1: 1, 3: 2}
    assert row.recall(3) == 0.5
    assert (row.out_of_scope_total, row.refused) == (2, 2)
    assert row.answer_rate == 0.75 and row.refusal_rate == 1.0
    assert row.balanced_accuracy == 0.875


def test_evaluate_with_empty_sections():
    row = evaluate([], 0.5, ks=(1,))
    assert row.recall(1) == 0.0 and row.refusal_rate == 0.0


def test_default_thresholds_span_task_range():
    assert DEFAULT_THRESHOLDS[0] == 0.3 and DEFAULT_THRESHOLDS[-1] == 0.7
    assert len(DEFAULT_THRESHOLDS) == 9


def test_sweep_and_recommend_picks_middle_of_first_best_plateau():
    observations = [
        obs(q("in1", A), 0.75, key(A)),
        obs(q("in2", A), 0.66, key(A)),
        obs(q("oos"), 0.52),
    ]
    rows = sweep(observations, thresholds=(0.5, 0.55, 0.6, 0.65, 0.7), ks=(1,))
    assert [r.balanced_accuracy for r in rows] == [0.5, 1.0, 1.0, 1.0, 0.75]
    assert recommend(rows).threshold == 0.6


def test_recommend_prefers_lowest_plateau_on_ties():
    observations = [obs(q("in", A), 0.6, key(A)), obs(q("oos"), 0.4)]
    rows = sweep(observations, thresholds=(0.3, 0.5, 0.7, 0.5), ks=(1,))
    # 0.3: 50%, 0.5: 100%, 0.7: 50%, 0.5 again: 100% (second plateau ignored)
    assert recommend(rows) is rows[1]
    with pytest.raises(ValueError):
        recommend([])


def test_score_summary():
    observations = [
        obs(q("in1", A), 0.7),
        obs(q("in2", A), 0.65),
        obs(q("in3", A), None),
        obs(q("oos1"), 0.5),
        obs(q("oos2"), None),
    ]
    assert score_summary(observations) == {
        "in_scope_min": 0.65,
        "in_scope_median": 0.7,
        "out_of_scope_max": 0.5,
    }


def test_tables_render_markdown():
    observations = [
        obs(q("in", A), 0.7, ("X", "Điều 1"), key(A)),
        obs(Question("oos", "a | b"), None),
    ]
    table = sweep_table(sweep(observations, thresholds=(0.6,), ks=(1, 3)), ks=(1, 3))
    assert "| 0.60 | 1/1 (100%) | 0% | 100% | 1/1 (100%) | 100% |" in table
    detail = detail_table(observations)
    row = "| in | câu in | 100/2015/QH13 Điều 173 | 0.700 | 2 | X Điều 1; 100/2015/QH13 Điều 173 |"
    assert row in detail
    assert "| oos | a \\| b | từ chối | — | — | — |" in detail
