import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.core.deps import get_qdrant
from app.db.models import Conversation, Message, UserApiKey, UserSettings
from app.db.session import get_session_factory
from app.ingest import indexer
from app.ingest.embedder import FakeEmbedder
from app.ingest.schemas import LegalDocumentIn
from app.llm import client as llm_client
from app.llm.errors import LLMError
from app.main import app
from app.rag import service as rag_service
from app.rag.citations import MODEL_REFUSAL
from app.rag.prompt import SYSTEM_PROMPT
from app.rag.service import DEFAULT_TITLE, REFUSAL_ANSWER, make_title

PASSWORD = "mat-khau-123"
API_KEY = "sk-test-0123456789abcdef-WXYZ"
MODEL_ID = "openai-main"
LUAT_MAU = (Path(__file__).parent / "fixtures" / "chunker" / "luat_mau.txt").read_text("utf-8")
QUESTION = "Cá nhân có quyền được thông tin về văn bản pháp luật không?"
UNRELATED = "Công thức nấu phở bò Nam Định gồm gia vị gì?"


# --- Pure logic ---------------------------------------------------------------------------


def test_make_title() -> None:
    assert make_title("  Hỏi   về\nluật  ") == "Hỏi về luật"
    long = "từ " * 40
    title = make_title(long)
    assert len(title) <= 61 and title.endswith("…")


# --- Fixtures -----------------------------------------------------------------------------


class FakeLLM:
    """Stands in for `llm_client.stream_chat`: yields `tokens`, then raises `error` if set."""

    def __init__(self) -> None:
        self.tokens: list[str] = ["Có [1]."]
        self.error: Exception | None = None
        self.calls: list[dict] = []

    async def __call__(
        self, *, model_id: str, api_key: str, messages: list[dict]
    ) -> AsyncIterator[str]:
        self.calls.append({"model_id": model_id, "api_key": api_key, "messages": messages})
        for token in self.tokens:
            yield token
        if self.error is not None:
            raise self.error


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr(llm_client, "stream_chat", fake)
    return fake


@pytest.fixture
async def qdrant(monkeypatch: pytest.MonkeyPatch):
    client = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    try:
        await client.get_collections()
    except Exception as exc:
        await client.close()
        pytest.skip(f"Qdrant not reachable ({type(exc).__name__}): run `docker compose up -d`")
    name = f"test_chat_{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(settings, "qdrant_collection", name)
    monkeypatch.setattr(settings, "rag_min_score", 0.3)
    try:
        await indexer.ensure_collection(client, FakeEmbedder())
        yield client
    finally:
        await client.delete_collection(name)
        await client.close()


@pytest.fixture
def session_factory(db_session: AsyncSession):
    @asynccontextmanager
    async def shared():
        yield db_session  # the stream shares the rolled-back test transaction

    return shared


@pytest.fixture
async def api(client, qdrant, session_factory, llm):
    app.dependency_overrides[get_qdrant] = lambda: qdrant
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    yield client
    app.dependency_overrides.pop(get_qdrant, None)
    app.dependency_overrides.pop(get_session_factory, None)


@pytest.fixture
async def law(db_session: AsyncSession, qdrant: AsyncQdrantClient) -> uuid.UUID:
    doc = LegalDocumentIn.model_validate(
        {
            "source": "vbpl",
            "source_id": "99",
            "title": "Luật Thử nghiệm",
            "so_hieu": "99/2025/QH15",
            "doc_type": "Luật",
            "issued_date": "2025-06-01",
            "effective_date": "2026-01-01",
            "status": "con_hieu_luc",
            "source_url": "https://vbpl.vn/van-ban/99",
        }
    )
    return await indexer.index_document(db_session, qdrant, doc, LUAT_MAU)


async def _register(client: httpx.AsyncClient, email: str) -> uuid.UUID:
    response = await client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


async def _ready_user(
    client: httpx.AsyncClient, session: AsyncSession, email: str = "a@example.com"
) -> uuid.UUID:
    """Registered (and logged in) user with an OpenAI key and `MODEL_ID` as default."""
    user_id = await _register(client, email)
    session.add(
        UserApiKey(
            user_id=user_id,
            provider="openai",
            encrypted_key=encrypt_secret(API_KEY),
            key_last4=API_KEY[-4:],
        )
    )
    session.add(UserSettings(user_id=user_id, default_provider="openai", default_model_id=MODEL_ID))
    await session.commit()
    return user_id


async def _conversation(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/chat/conversations")
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == DEFAULT_TITLE
    return body["id"]


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        name, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = json.loads(line.removeprefix("data:").strip())
        if name is not None:
            events.append((name, data))
    return events


async def _ask(
    client: httpx.AsyncClient, conversation_id: str, content: str, **extra: object
) -> list[tuple[str, dict]]:
    response = await client.post(
        f"/api/chat/conversations/{conversation_id}/messages",
        json={"content": content, **extra},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return _parse_sse(response.text)


async def _messages(session: AsyncSession, conversation_id: str) -> list[Message]:
    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == uuid.UUID(conversation_id))
        .order_by(Message.created_at)
    )
    return list(rows)


# --- Answer stream ------------------------------------------------------------------------


async def test_answer_streams_events_in_order(api, db_session, law, llm) -> None:
    user_id = await _ready_user(api, db_session)
    conversation_id = await _conversation(api)
    llm.tokens = ["Cá nhân ", "có quyền được thông tin [1]", " về văn bản [1][9]."]

    events = await _ask(api, conversation_id, QUESTION)

    assert [name for name, _ in events] == ["start", "token", "token", "token", "citations", "done"]
    start = events[0][1]
    assert start["model_id"] == MODEL_ID
    assert "".join(data["t"] for name, data in events if name == "token") == "".join(llm.tokens)
    citations = events[4][1]
    assert citations["uncited"] is False
    assert [c["n"] for c in citations["citations"]] == [1]  # [9] does not exist: dropped
    citation = citations["citations"][0]
    # Rule 2: link, dates and status come from the DB.
    assert citation["document_id"] == str(law)
    assert citation["source_url"] == "https://vbpl.vn/van-ban/99"
    assert citation["issued_date"] == "2025-06-01"
    assert citation["effective_date"] == "2026-01-01"
    assert citation["status"] == "con_hieu_luc"
    assert citation["status_label"] == "Còn hiệu lực"
    assert events[5][1] == {"refused": False}

    # The LLM got the decrypted key, the system prompt and numbered sources.
    [call] = llm.calls
    assert call["model_id"] == MODEL_ID
    assert call["api_key"] == API_KEY
    assert call["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert call["messages"][-1]["role"] == "user"
    assert "NGUỒN:" in call["messages"][-1]["content"]
    assert "[1] Luật Thử nghiệm (99/2025/QH15)" in call["messages"][-1]["content"]
    assert call["messages"][-1]["content"].endswith(f"CÂU HỎI: {QUESTION}")

    # Both messages are saved, the assistant one with its citations.
    user_msg, assistant = await _messages(db_session, conversation_id)
    assert (user_msg.role, user_msg.content) == ("user", QUESTION)
    assert assistant.id == uuid.UUID(start["message_id"])
    assert assistant.role == "assistant"
    assert assistant.content == "".join(llm.tokens)
    assert (assistant.provider, assistant.model_id) == ("openai", MODEL_ID)
    assert assistant.citations == citations["citations"]
    assert assistant.retrieved_chunk_ids
    assert assistant.refused is False and assistant.error is None

    key = await db_session.scalar(select(UserApiKey).where(UserApiKey.user_id == user_id))
    await db_session.refresh(key)
    assert key.last_used_at is not None
    conversation = await db_session.get(Conversation, uuid.UUID(conversation_id))
    await db_session.refresh(conversation)
    assert conversation.title == QUESTION

    # History endpoint returns the same citations.
    response = await api.get(f"/api/chat/conversations/{conversation_id}/messages")
    assert response.status_code == 200
    history = response.json()
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["citations"] == citations["citations"]
    assert history[1]["error"] is None


async def test_below_threshold_refuses_without_llm(api, db_session, law, llm) -> None:
    await _ready_user(api, db_session)
    conversation_id = await _conversation(api)

    events = await _ask(api, conversation_id, UNRELATED)

    assert llm.calls == []  # rule 3
    assert [name for name, _ in events] == ["start", "token", "citations", "done"]
    assert events[1][1] == {"t": REFUSAL_ANSWER}
    assert events[2][1] == {"citations": [], "uncited": False}
    assert events[3][1] == {"refused": True}
    _, assistant = await _messages(db_session, conversation_id)
    assert assistant.refused is True
    assert assistant.content == REFUSAL_ANSWER
    assert assistant.citations == [] and assistant.retrieved_chunk_ids == []


async def test_empty_collection_refuses_without_llm(api, db_session, llm) -> None:
    await _ready_user(api, db_session)
    conversation_id = await _conversation(api)
    events = await _ask(api, conversation_id, QUESTION)
    assert llm.calls == []
    assert events[-1] == ("done", {"refused": True})


async def test_no_source_fits_budget_refuses_without_llm(
    api, db_session, law, llm, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrieval found sources but none fits the context budget: still a refusal (rule 3)."""
    await _ready_user(api, db_session)
    conversation_id = await _conversation(api)
    build_prompt = rag_service.build_prompt

    def tiny_budget(question, blocks, history, *, max_context_tokens):
        return build_prompt(question, blocks, history, max_context_tokens=1)

    monkeypatch.setattr(rag_service, "build_prompt", tiny_budget)

    events = await _ask(api, conversation_id, QUESTION)

    assert llm.calls == []
    assert [name for name, _ in events] == ["start", "token", "citations", "done"]
    assert events[-1] == ("done", {"refused": True})
    _, assistant = await _messages(db_session, conversation_id)
    assert assistant.refused is True and assistant.content == REFUSAL_ANSWER


async def test_save_failure_ends_with_error_not_citations(
    api, db_session, law, llm, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _ready_user(api, db_session)
    conversation_id = await _conversation(api)

    async def broken_save(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(rag_service, "_save_reply", broken_save)

    events = await _ask(api, conversation_id, QUESTION)

    names = [name for name, _ in events]
    assert names[0] == "start" and "citations" not in names and "done" not in names
    assert events[-1][0] == "error" and events[-1][1]["code"] == "save_failed"


async def test_first_question_keeps_user_rename(api, db_session, law, llm) -> None:
    await _ready_user(api, db_session)
    conversation_id = await _conversation(api)
    url = f"/api/chat/conversations/{conversation_id}"
    assert (await api.patch(url, json={"title": "Thuế TNCN"})).status_code == 200

    await _ask(api, conversation_id, QUESTION)

    conversation = await db_session.get(Conversation, uuid.UUID(conversation_id))
    await db_session.refresh(conversation)
    assert conversation.title == "Thuế TNCN"


async def test_no_api_key(api, db_session, law, llm) -> None:
    user_id = await _register(api, "a@example.com")
    db_session.add(
        UserSettings(user_id=user_id, default_provider="openai", default_model_id=MODEL_ID)
    )
    await db_session.commit()
    conversation_id = await _conversation(api)

    events = await _ask(api, conversation_id, QUESTION)

    assert [name for name, _ in events] == ["error"]
    assert events[0][1]["code"] == "no_api_key"
    assert "OpenAI" in events[0][1]["message"]
    assert llm.calls == []
    assert await _messages(db_session, conversation_id) == []


async def test_no_model(api, db_session, law, llm) -> None:
    await _register(api, "a@example.com")
    conversation_id = await _conversation(api)
    events = await _ask(api, conversation_id, QUESTION)
    assert [(name, data["code"]) for name, data in events] == [("error", "no_model")]
    assert llm.calls == []
    assert await _messages(db_session, conversation_id) == []


async def test_model_from_request(api, db_session, law, llm) -> None:
    await _ready_user(api, db_session)
    conversation_id = await _conversation(api)

    events = await _ask(api, conversation_id, QUESTION, model_id="khong-ton-tai")
    assert [(name, data["code"]) for name, data in events] == [("error", "unknown_model")]

    # A known model of a provider without key: no_api_key, even with a usable default.
    events = await _ask(api, conversation_id, QUESTION, model_id="anthropic-main")
    assert [(name, data["code"]) for name, data in events] == [("error", "no_api_key")]
    assert llm.calls == []

    events = await _ask(api, conversation_id, QUESTION, model_id=MODEL_ID)
    assert events[0] == ("start", {"message_id": events[0][1]["message_id"], "model_id": MODEL_ID})


async def test_llm_error_mid_stream_saves_partial_answer(api, db_session, law, llm) -> None:
    user_id = await _ready_user(api, db_session)
    conversation_id = await _conversation(api)
    llm.tokens = ["Cá nhân có quyền [1]"]
    llm.error = LLMError("rate_limited")

    events = await _ask(api, conversation_id, QUESTION)

    assert [name for name, _ in events] == ["start", "token", "error"]
    assert events[2][1]["code"] == "rate_limited"
    assert events[2][1]["message"] == LLMError("rate_limited").user_message
    _, assistant = await _messages(db_session, conversation_id)
    assert assistant.content == "Cá nhân có quyền [1]"
    assert assistant.error == "rate_limited"
    assert assistant.citations == []
    key = await db_session.scalar(select(UserApiKey).where(UserApiKey.user_id == user_id))
    await db_session.refresh(key)
    assert key.last_used_at is None


async def test_unexpected_llm_exception_is_provider_error(api, db_session, law, llm) -> None:
    await _ready_user(api, db_session)
    conversation_id = await _conversation(api)
    llm.tokens = []
    llm.error = RuntimeError("secret details sk-should-not-leak")

    events = await _ask(api, conversation_id, QUESTION)

    assert [name for name, _ in events] == ["start", "error"]
    assert events[1][1]["code"] == "provider_error"
    assert "sk-should-not-leak" not in json.dumps(events, ensure_ascii=False)
    _, assistant = await _messages(db_session, conversation_id)
    assert (assistant.content, assistant.error) == ("", "provider_error")


async def test_answer_without_markers_gets_uncited_sources(api, db_session, law, llm) -> None:
    await _ready_user(api, db_session)
    conversation_id = await _conversation(api)
    llm.tokens = ["Cá nhân có quyền được thông tin."]

    events = await _ask(api, conversation_id, QUESTION)

    name, data = events[-2]
    assert name == "citations"
    assert data["uncited"] is True
    assert 1 <= len(data["citations"]) <= 3
    assert all(c["uncited"] for c in data["citations"])
    assert events[-1] == ("done", {"refused": False})


async def test_model_refusal(api, db_session, law, llm) -> None:
    await _ready_user(api, db_session)
    conversation_id = await _conversation(api)
    llm.tokens = [f"{MODEL_REFUSAL}."]

    events = await _ask(api, conversation_id, QUESTION)

    assert events[-2] == ("citations", {"citations": [], "uncited": False})
    assert events[-1] == ("done", {"refused": True})
    _, assistant = await _messages(db_session, conversation_id)
    assert assistant.refused is True


async def test_history_is_sent_and_short_question_uses_previous(
    api, db_session, law, llm, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _ready_user(api, db_session)
    conversation_id = await _conversation(api)
    llm.tokens = ["Có quyền được thông tin [1]."]
    # A refused exchange is not repeated to the LLM.
    await _ask(api, conversation_id, UNRELATED)
    await _ask(api, conversation_id, QUESTION)

    queries: list[str] = []
    retrieve = rag_service.retrieve

    async def spy(session, qdrant, query, filters, *args, **kwargs):
        queries.append(query)
        return await retrieve(session, qdrant, query, filters, *args, **kwargs)

    monkeypatch.setattr(rag_service, "retrieve", spy)
    await _ask(api, conversation_id, "Còn nghĩa vụ thì sao?")

    # Short follow-up: the previous user question is prepended to the retrieval query.
    assert queries == [f"{QUESTION}\nCòn nghĩa vụ thì sao?"]
    messages = llm.calls[-1]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == QUESTION
    assert messages[2]["content"] == "Có quyền được thông tin."  # old markers stripped
    # The conversation keeps the title of its first question.
    conversation = await db_session.get(Conversation, uuid.UUID(conversation_id))
    await db_session.refresh(conversation)
    assert conversation.title == UNRELATED


async def test_stream_cancelled_saves_partial_answer(db_session, qdrant, law, llm) -> None:
    """Stop button: the generator is closed after the first token."""
    from app.auth.service import create_user

    user = await create_user(db_session, "a@example.com", PASSWORD)
    db_session.add(
        UserApiKey(
            user_id=user.id,
            provider="openai",
            encrypted_key=encrypt_secret(API_KEY),
            key_last4="WXYZ",
        )
    )
    db_session.add(
        UserSettings(user_id=user.id, default_provider="openai", default_model_id=MODEL_ID)
    )
    conversation = Conversation(user_id=user.id, title=DEFAULT_TITLE)
    db_session.add(conversation)
    await db_session.commit()
    llm.tokens = ["Phần đầu [1]", " phần sau"]

    events = rag_service.answer(
        db_session, qdrant, user_id=user.id, conversation_id=conversation.id, question=QUESTION
    )
    seen = []
    async for event in events:
        seen.append(event.event)
        if event.event == "token":
            break
    await events.aclose()

    assert seen == ["start", "token"]
    _, assistant = await _messages(db_session, str(conversation.id))
    assert assistant.content == "Phần đầu [1]"
    assert assistant.error == "cancelled"


# --- Conversations ------------------------------------------------------------------------


async def test_chat_requires_login(api) -> None:
    assert (await api.get("/api/chat/conversations")).status_code == 401
    assert (await api.post("/api/chat/conversations")).status_code == 401


async def test_conversation_crud(api, db_session, law, llm) -> None:
    await _ready_user(api, db_session)
    first = await _conversation(api)
    second = await _conversation(api)
    # A new message moves its conversation to the top.
    await _ask(api, first, QUESTION)

    response = await api.get("/api/chat/conversations")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [c["id"] for c in body["items"]] == [first, second]
    assert body["items"][0]["title"] == QUESTION

    response = await api.patch(f"/api/chat/conversations/{second}", json={"title": "  Tên mới "})
    assert response.status_code == 200
    assert response.json()["title"] == "Tên mới"
    response = await api.patch(f"/api/chat/conversations/{second}", json={"title": "  "})
    assert response.status_code == 422

    assert (await api.delete(f"/api/chat/conversations/{first}")).status_code == 204
    assert await _messages(db_session, first) == []  # cascade
    response = await api.get("/api/chat/conversations")
    assert [c["id"] for c in response.json()["items"]] == [second]
    assert (await api.delete(f"/api/chat/conversations/{first}")).status_code == 404


async def test_conversations_are_private(api, db_session, law, llm) -> None:
    await _ready_user(api, db_session, "a@example.com")
    conversation_id = await _conversation(api)
    await _ask(api, conversation_id, QUESTION)

    await _ready_user(api, db_session, "b@example.com")  # now logged in as B
    assert (await api.get("/api/chat/conversations")).json() == {"items": [], "total": 0}
    url = f"/api/chat/conversations/{conversation_id}"
    assert (await api.get(f"{url}/messages")).status_code == 404
    assert (await api.patch(url, json={"title": "x"})).status_code == 404
    assert (await api.delete(url)).status_code == 404
    response = await api.post(f"{url}/messages", json={"content": QUESTION})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert len(llm.calls) == 1
    assert len(await _messages(db_session, conversation_id)) == 2


async def test_message_validation(api, db_session) -> None:
    await _ready_user(api, db_session)
    conversation_id = await _conversation(api)
    url = f"/api/chat/conversations/{conversation_id}/messages"
    assert (await api.post(url, json={"content": "   "})).status_code == 422
    assert (await api.post(url, json={"content": "x" * 4001})).status_code == 422
    assert (
        await api.post(f"/api/chat/conversations/{uuid.uuid4()}/messages", json={"content": "a"})
    ).status_code == 404
