"""Prompt construction (docs/05-rag-chat.md, "Prompt").

The LLM sees numbered sources `[1]`, `[2]`, ... built from `SourceBlock`s and is told to
cite them by number only: links, dates and validity shown to the user come from the DB
(`app.rag.citations`), never from the model's text.

Token budget: 70% of the model's `max_context_tokens` (the rest is left for the answer and
for the error of the estimate). Over budget, history goes first (oldest exchange first), then
sources from the end of the ranking. The best source always stays, cut if it must.

History is sent as complete user -> assistant exchanges only, so roles always alternate
(system, user, assistant, ..., user), which Anthropic and Gemini require. Assistant answers
lose their `[n]` markers: those numbered the sources of their own turn, not this one.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, TypedDict

from app.ingest.schemas import STATUS_LABELS
from app.rag.citations import MODEL_REFUSAL, strip_markers
from app.rag.retriever import SourceBlock

SYSTEM_PROMPT = f"""Bạn là trợ lý tra cứu pháp luật Việt Nam.
QUY TẮC:
1. Chỉ trả lời dựa trên các trích đoạn văn bản được cung cấp trong phần NGUỒN. Không dùng kiến thức bên ngoài.
2. Mỗi ý trong câu trả lời phải kèm số nguồn dạng [1], [2] ngay sau ý đó.
3. Nếu các nguồn không đủ để trả lời, nói rõ: "{MODEL_REFUSAL}." và không suy đoán.
4. Không tự viết link, ngày ban hành, ngày hiệu lực. Hệ thống sẽ tự hiển thị các thông tin này theo số nguồn.
5. Nếu nguồn có ghi chú tình trạng khác "còn hiệu lực", hãy nhắc người dùng lưu ý.
6. Nếu nguồn là "tài liệu người dùng tải lên", nói rõ đây không phải nguồn chính thống.
7. Trả lời bằng tiếng Việt, ngắn gọn, đi thẳng vào câu hỏi. Trích nguyên văn quy định khi cần độ chính xác."""  # noqa: E501

SOURCE_LABELS = {"vbpl": "vbpl.vn", "user_upload": "tài liệu người dùng tải lên"}
MAX_HISTORY_MESSAGES = 6
CONTEXT_BUDGET_RATIO = 0.7
# Below this many words, the previous user question is added to the retrieval query.
SHORT_QUESTION_WORDS = 8
# Cut mark when the only source left does not fit.
TRUNCATED = "\n[...]"
# Overhead of one chat message (role, separators) in the estimate.
MESSAGE_OVERHEAD_TOKENS = 4

TokenCounter = Callable[[str], int]


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


def _is_latin(char: str) -> bool:
    # ASCII, Latin-1 / Extended-A/B, combining diacritics, Latin Extended Additional (the
    # precomposed Vietnamese letters "ạ", "ế", "ữ", ...).
    code = ord(char)
    return code <= 0x24F or 0x300 <= code <= 0x36F or 0x1E00 <= code <= 0x1EFF


def estimate_tokens(text: str) -> int:
    """Rough estimate of the providers' token count, not a guarantee.

    The chat model's tokenizer is not known locally (it depends on the provider), and
    Vietnamese with diacritics costs about one token per 2-3 characters on current
    tokenizers: Latin characters count half a token. Other scripts (CJK, emoji, symbols)
    can cost a token or more per character, so they count one each. The 30% of the context
    left outside the budget absorbs the error; pass `count_tokens` to `build_prompt` for an
    exact count (e.g. LiteLLM's `token_counter`)."""
    other = sum(1 for char in text if not _is_latin(char))
    return math.ceil((len(text) - other) / 2) + other


@dataclass
class Prompt:
    messages: list[ChatMessage]
    # The sources actually sent, in order: `blocks[n - 1]` is source `[n]`.
    blocks: list[SourceBlock]
    # Estimated tokens of `messages`.
    tokens: int
    # Messages of `history` not sent (outside the window, not in an exchange, over budget).
    history_dropped: int = 0
    blocks_dropped: int = 0
    # The last block sent was cut to fit.
    truncated: bool = False


def source_header(n: int, block: SourceBlock) -> str:
    doc = block.document
    header = f"[{n}] {doc.title}"
    if doc.so_hieu:
        header += f" ({doc.so_hieu})"
    location = ". ".join(part for part in (block.dieu, block.dieu_title) if part)
    if location:
        header += f" — {location}"
    status = STATUS_LABELS.get(doc.status, doc.status)
    source = SOURCE_LABELS.get(doc.source, doc.source)
    return f"{header}\nTình trạng: {status} | Nguồn: {source}\n---\n"


def format_source(n: int, block: SourceBlock, text: str | None = None) -> str:
    """Source `[n]` as the LLM reads it; `text` replaces the block text (when cut)."""
    return source_header(n, block) + (block.text if text is None else text)


def user_content(question: str, sources: Sequence[str]) -> str:
    body = "\n\n".join(sources) if sources else "(không có)"
    return f"NGUỒN:\n\n{body}\n\nCÂU HỎI: {question}"


def _cut(text: str, budget: int, count: TokenCounter) -> tuple[str, bool]:
    """`text` if it fits `budget` tokens, else its longest prefix that fits with the cut
    mark, found by bisection and ended on a whitespace (space or line break). Returns the
    text and whether it was cut; "" if nothing fits."""
    if count(text) <= budget:
        return text, False
    if budget <= count(TRUNCATED):
        return "", False
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if count(text[:mid] + TRUNCATED) <= budget:
            low = mid
        else:
            high = mid - 1
    prefix = text[:low]
    # Do not end in the middle of a word (a word directly after the cut is not cut).
    if not text[low].isspace():
        boundary = max((i for i, char in enumerate(prefix) if char.isspace()), default=0)
        prefix = prefix[:boundary] if boundary > 0 else prefix
    prefix = prefix.rstrip()
    return (prefix + TRUNCATED, True) if prefix else ("", False)


def history_exchanges(history: Sequence[ChatMessage]) -> list[tuple[str, str]]:
    """(question, answer) pairs from `history`, oldest first: a user message directly
    followed by an assistant message. Anything else (a question left without answer, an
    answer without question, system or empty messages) is skipped. Answers lose their
    `[n]` markers."""
    turns = [m for m in history if m["role"] in ("user", "assistant") and m["content"].strip()]
    pairs: list[tuple[str, str]] = []
    for previous, current in zip(turns, turns[1:], strict=False):
        if previous["role"] == "user" and current["role"] == "assistant":
            answer = strip_markers(current["content"]).strip()
            if answer:
                pairs.append((previous["content"], answer))
    return pairs


def build_prompt(
    question: str,
    blocks: Sequence[SourceBlock],
    history: Sequence[ChatMessage] = (),
    *,
    max_context_tokens: int,
    count_tokens: TokenCounter = estimate_tokens,
) -> Prompt:
    """Chat messages for `question`: system prompt, the last exchanges of `history` (at
    most `MAX_HISTORY_MESSAGES` messages, see `history_exchanges`), then sources and
    question in one user message.

    `blocks` are ranked best first (`RetrievalResult.blocks`)."""
    budget = int(max_context_tokens * CONTEXT_BUDGET_RATIO)

    def cost(content: str) -> int:
        return count_tokens(content) + MESSAGE_OVERHEAD_TOKENS

    pairs = history_exchanges(history)[-(MAX_HISTORY_MESSAGES // 2) :]
    sources = [format_source(n, block) for n, block in enumerate(blocks, start=1)]
    # Each source adds its text plus the "\n\n" separator.
    source_costs = [count_tokens(s) + count_tokens("\n\n") for s in sources]

    # [""]: the frame of the user message without the "(không có)" placeholder.
    fixed = cost(SYSTEM_PROMPT) + cost(user_content(question, [""]))
    history_cost = sum(cost(q) + cost(a) for q, a in pairs)
    sources_cost = sum(source_costs)

    # 1. History first, oldest exchange first.
    while pairs and fixed + history_cost + sources_cost > budget:
        q, a = pairs.pop(0)
        history_cost -= cost(q) + cost(a)
    # 2. Then sources from the end, keeping the best one.
    kept = len(sources)
    while kept > 1 and fixed + history_cost + sources_cost > budget:
        kept -= 1
        sources_cost -= source_costs[kept]
    sources = sources[:kept]
    truncated = False
    if sources and fixed + history_cost + sources_cost > budget:
        # 3. The best source alone does not fit: cut its text.
        block = blocks[0]
        header = source_header(1, block)
        # Measured on the real user message, not on the sum of its parts.
        text, truncated = _cut(
            block.text,
            budget - cost(SYSTEM_PROMPT) - history_cost,
            lambda t: cost(user_content(question, [header + t])),
        )
        sources = [header + text] if text else []
        kept = len(sources)

    turns = [
        message
        for q, a in pairs
        for message in (
            ChatMessage(role="user", content=q),
            ChatMessage(role="assistant", content=a),
        )
    ]
    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        *turns,
        ChatMessage(role="user", content=user_content(question, sources)),
    ]
    return Prompt(
        messages=messages,
        blocks=list(blocks[:kept]),
        tokens=sum(cost(m["content"]) for m in messages),
        # Every history message not sent: beyond the window, malformed or over budget.
        history_dropped=len(history) - len(turns),
        blocks_dropped=len(blocks) - kept,
        truncated=truncated,
    )


def retrieval_query(question: str, previous_question: str | None) -> str:
    """Text to embed: a short follow-up question ("thế còn trường hợp kia?") gets the
    previous user question prepended, so the search keeps its subject."""
    if previous_question and len(question.split()) < SHORT_QUESTION_WORDS:
        return f"{previous_question}\n{question}"
    return question
