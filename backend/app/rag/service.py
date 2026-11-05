"""Chat service: one question in, a stream of chat events out (docs/05-rag-chat.md, "Chat
service"; events in docs/06-api.md, "Chat").

Events, in order:
- normal answer: `start -> token* -> citations -> done`
- below the retrieval threshold, or no source fits the context budget:
  `start -> token (fixed refusal) -> citations [] -> done`, the LLM is never called (rule 3)
- no model / no key / conversation gone: a single `error`, nothing is saved
- failure after `start` (retrieval, LLM, saving): `start -> token* -> error`; the assistant
  message is saved (when the DB allows) with what was generated and `error` set

`citations` and `done` are sent only once the assistant message is committed, so a client never
shows a complete answer that is missing after a reload.

Once `start` announced `message_id`, the assistant message is always saved, also when the
client goes away (stop button): it then keeps the partial text with `error="cancelled"`.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio
from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.db.models import PROVIDER_LABELS, Conversation, Message, UserApiKey, UserSettings
from app.llm import client as llm_client
from app.llm.errors import USER_MESSAGES, LLMError
from app.llm.registry import ModelRegistry, ModelSpec, get_registry
from app.rag.citations import build_citations, extract_markers, is_refusal
from app.rag.prompt import ChatMessage, build_prompt, retrieval_query
from app.rag.retriever import RetrievalFilters, retrieve

logger = logging.getLogger(__name__)

# Shown (and saved) when retrieval finds nothing above the threshold.
REFUSAL_ANSWER = (
    "Không tìm thấy quy định liên quan trong cơ sở dữ liệu hiện có. Bạn thử diễn đạt lại câu "
    "hỏi, nêu rõ tên hoặc số hiệu văn bản, hoặc tải văn bản liên quan lên ở tab Văn bản."
)
DEFAULT_TITLE = "Cuộc hội thoại mới"
TITLE_CHARS = 60
# Messages read back for the prompt history (build_prompt keeps the last exchanges).
HISTORY_LOAD = 20
# Saving the partial answer of a cancelled stream must not hold the shutdown forever.
CANCEL_SAVE_TIMEOUT_S = 5.0

CHAT_ERROR_MESSAGES = {
    "not_found": "Không tìm thấy cuộc hội thoại.",
    "no_model": "Bạn chưa chọn model. Vào Cài đặt để chọn model mặc định.",
    "unknown_model": "Model không tồn tại hoặc đã bị tắt. Chọn model khác.",
    "no_api_key": "Bạn chưa nhập API key cho {provider}. Vào Cài đặt để thêm key.",
    "key_unreadable": "Không đọc được API key đã lưu. Vào Cài đặt để nhập lại key.",
    "retrieval_failed": "Không truy vấn được cơ sở dữ liệu văn bản. Vui lòng thử lại sau.",
    "save_failed": "Không lưu được câu trả lời. Vui lòng thử lại sau.",
}

EventName = Literal["start", "token", "citations", "error", "done"]


@dataclass(frozen=True)
class ChatEvent:
    event: EventName
    data: dict[str, Any]


class ChatError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def _error(code: str, message: str) -> ChatEvent:
    return ChatEvent("error", {"code": code, "message": message})


def make_title(question: str) -> str:
    flat = " ".join(question.split())
    return flat if len(flat) <= TITLE_CHARS else flat[:TITLE_CHARS].rstrip() + "…"


@dataclass
class _Reply:
    """The assistant message being built."""

    id: uuid.UUID
    conversation_id: uuid.UUID
    spec: ModelSpec
    parts: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    chunk_ids: list[uuid.UUID] = field(default_factory=list)
    refused: bool = False
    error: str | None = None
    saved: bool = False

    @property
    def content(self) -> str:
        return "".join(self.parts)


async def _resolve_model(
    session: AsyncSession, user_id: uuid.UUID, model_id: str | None, registry: ModelRegistry
) -> ModelSpec:
    if model_id is not None:
        spec = registry.get_enabled(model_id)
        if spec is None:
            raise ChatError("unknown_model", CHAT_ERROR_MESSAGES["unknown_model"])
        return spec
    default = await session.scalar(
        select(UserSettings.default_model_id).where(UserSettings.user_id == user_id)
    )
    spec = registry.get_enabled(default) if default else None
    if spec is None:
        raise ChatError("no_model", CHAT_ERROR_MESSAGES["no_model"])
    return spec


async def _load_key(
    session: AsyncSession, user_id: uuid.UUID, spec: ModelSpec
) -> tuple[str, uuid.UUID]:
    row = (
        await session.execute(
            select(UserApiKey.id, UserApiKey.encrypted_key).where(
                UserApiKey.user_id == user_id, UserApiKey.provider == spec.provider
            )
        )
    ).one_or_none()
    if row is None:
        message = CHAT_ERROR_MESSAGES["no_api_key"].format(provider=PROVIDER_LABELS[spec.provider])
        raise ChatError("no_api_key", message)
    try:
        return decrypt_secret(row.encrypted_key), row.id
    except Exception as exc:
        # Master key rotated out without re-encrypting: never log the token itself.
        logger.warning("api key not decryptable: user=%s error=%s", user_id, type(exc).__name__)
        raise ChatError("key_unreadable", CHAT_ERROR_MESSAGES["key_unreadable"]) from None


async def _history(
    session: AsyncSession, conversation_id: uuid.UUID
) -> tuple[list[ChatMessage], str | None, int]:
    """(prompt history, previous user question, number of messages so far).

    Refused and unfinished answers are left out: they carry no content worth repeating, and
    their question is then dropped too (`history_exchanges` keeps complete pairs only)."""
    rows = list(
        await session.execute(
            select(Message.role, Message.content, Message.refused, Message.error)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(HISTORY_LOAD)
        )
    )
    rows.reverse()
    previous = next((m.content for m in reversed(rows) if m.role == "user"), None)
    history = [
        ChatMessage(role=m.role, content=m.content)  # type: ignore[typeddict-item]
        for m in rows
        if m.role == "user" or (not m.refused and m.error is None)
    ]
    return history, previous, len(rows)


async def _save_reply(
    session: AsyncSession, reply: _Reply, key_id: uuid.UUID | None = None
) -> None:
    session.add(
        Message(
            id=reply.id,
            conversation_id=reply.conversation_id,
            role="assistant",
            content=reply.content,
            provider=reply.spec.provider,
            model_id=reply.spec.id,
            citations=reply.citations,
            retrieved_chunk_ids=reply.chunk_ids,
            refused=reply.refused,
            error=reply.error,
        )
    )
    if key_id is not None:
        await session.execute(
            update(UserApiKey)
            .where(UserApiKey.id == key_id)
            .values(last_used_at=func.clock_timestamp())
        )
    await session.commit()
    reply.saved = True


async def _try_save(session: AsyncSession, reply: _Reply, key_id: uuid.UUID | None = None) -> bool:
    """Save the reply; False (logged) when the DB fails, so the stream can still end cleanly."""
    try:
        await session.rollback()  # a failed or cancelled operation may have left a transaction
        await _save_reply(session, reply, key_id)
        return True
    except Exception as exc:
        logger.warning("could not save answer %s: %s", reply.id, type(exc).__name__)
        return False


async def _save_cancelled(session: AsyncSession, reply: _Reply) -> None:
    """Save the partial answer while the request is being cancelled."""
    # Keep a more precise error (e.g. invalid_key) when the cancel hit while saving it.
    reply.error = reply.error or "cancelled"
    # Shielded: under anyio (sse-starlette), every await of a cancelled scope is cancelled again.
    with anyio.CancelScope(shield=True), anyio.move_on_after(CANCEL_SAVE_TIMEOUT_S):
        await _try_save(session, reply)


async def answer(
    session: AsyncSession,
    qdrant: AsyncQdrantClient,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    question: str,
    model_id: str | None = None,
    registry: ModelRegistry | None = None,
) -> AsyncIterator[ChatEvent]:
    """Answer `question` in the user's conversation (see the module docstring for events)."""
    registry = registry or get_registry()
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    if conversation is None:
        yield _error("not_found", CHAT_ERROR_MESSAGES["not_found"])
        return
    try:
        spec = await _resolve_model(session, user_id, model_id, registry)
        api_key, key_id = await _load_key(session, user_id, spec)
    except ChatError as exc:
        await session.rollback()
        yield _error(exc.code, exc.message)
        return

    history, previous_question, message_count = await _history(session, conversation_id)
    if message_count == 0 and conversation.title == DEFAULT_TITLE:  # keep a user rename
        conversation.title = make_title(question)
    session.add(Message(conversation_id=conversation_id, role="user", content=question))
    await session.commit()

    reply = _Reply(id=uuid.uuid4(), conversation_id=conversation_id, spec=spec)
    try:
        yield ChatEvent("start", {"message_id": str(reply.id), "model_id": spec.id})

        try:
            result = await retrieve(
                session,
                qdrant,
                retrieval_query(question, previous_question),
                RetrievalFilters(user_id=user_id),
            )
        except Exception:
            # No API key goes through retrieval: the full traceback is safe to log.
            logger.exception("retrieval failed")
            reply.error = "retrieval_failed"
            await _try_save(session, reply)
            yield _error("retrieval_failed", CHAT_ERROR_MESSAGES["retrieval_failed"])
            return
        reply.chunk_ids = [retrieved.chunk.id for retrieved in result.chunks]
        # End the read transaction: do not hold a connection idle during the LLM call.
        await session.commit()

        prompt = (
            build_prompt(
                question, result.blocks, history, max_context_tokens=spec.max_context_tokens
            )
            if result.found
            else None
        )
        if prompt is None or not prompt.blocks:
            # Rule 3: refuse without calling the LLM, also when no source fits the context
            # budget (a very long question on a small model).
            reply.parts.append(REFUSAL_ANSWER)
            reply.refused = True
            yield ChatEvent("token", {"t": REFUSAL_ANSWER})
            if not await _try_save(session, reply):
                yield _error("save_failed", CHAT_ERROR_MESSAGES["save_failed"])
                return
            yield ChatEvent("citations", {"citations": [], "uncited": False})
            yield ChatEvent("done", {"refused": True})
            return
        try:
            async with aclosing(
                llm_client.stream_chat(
                    model_id=spec.id, api_key=api_key, messages=list(prompt.messages)
                )
            ) as tokens:
                async for text in tokens:
                    reply.parts.append(text)
                    yield ChatEvent("token", {"t": text})
        except Exception as exc:
            error = exc if isinstance(exc, LLMError) else None
            if error is None:
                logger.warning("chat stream failed: %s", type(exc).__name__)
                error = LLMError("provider_error", USER_MESSAGES["provider_error"])
            reply.error = error.code
            await _try_save(session, reply)
            yield _error(error.code, error.user_message)
            return

        text = reply.content
        reply.refused = is_refusal(text, len(prompt.blocks))
        citations = build_citations(extract_markers(text), prompt.blocks, refused=reply.refused)
        reply.citations = [c.model_dump(mode="json") for c in citations]
        uncited = bool(citations) and citations[0].uncited
        if not await _try_save(session, reply, key_id):
            yield _error("save_failed", CHAT_ERROR_MESSAGES["save_failed"])
            return
        yield ChatEvent("citations", {"citations": reply.citations, "uncited": uncited})
        yield ChatEvent("done", {"refused": reply.refused})
    except (GeneratorExit, asyncio.CancelledError):
        # Client gone (stop button, closed tab): keep what was generated.
        if not reply.saved:
            await _save_cancelled(session, reply)
        raise
