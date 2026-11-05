"""Conversation and SSE message routes (docs/06-api.md, "Chat")."""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Query, status
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.chat import service
from app.chat.schemas import (
    ConversationList,
    ConversationOut,
    ConversationPatch,
    MessageIn,
    MessageOut,
)
from app.core.deps import CurrentUser, QdrantDep, SessionDep, SessionFactoryDep
from app.rag import service as rag_service

router = APIRouter(prefix="/chat", tags=["chat"])

# Comment line every N seconds so proxies do not close a stream waiting on the LLM.
SSE_PING_S = 15


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(user: CurrentUser, session: SessionDep) -> ConversationOut:
    return await service.create_conversation(session, user.id)


@router.get("/conversations")
async def list_conversations(
    user: CurrentUser, session: SessionDep, page: Annotated[int, Query(ge=1)] = 1
) -> ConversationList:
    return await service.list_conversations(session, user.id, page)


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[MessageOut]:
    return await service.list_messages(session, user.id, conversation_id)


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: uuid.UUID, body: ConversationPatch, user: CurrentUser, session: SessionDep
) -> ConversationOut:
    return await service.rename_conversation(session, user.id, conversation_id, body.title)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> None:
    await service.delete_conversation(session, user.id, conversation_id)


@router.post("/conversations/{conversation_id}/messages")
async def post_message(
    conversation_id: uuid.UUID,
    body: MessageIn,
    user: CurrentUser,
    session: SessionDep,
    session_factory: SessionFactoryDep,
    qdrant: QdrantDep,
) -> EventSourceResponse:
    """SSE stream of the answer. A missing conversation is a plain 404 before the stream."""
    user_id = user.id
    await service.get_conversation(session, user_id, conversation_id)
    # The request session would otherwise hold a pooled connection for the whole stream.
    await session.close()

    async def events() -> AsyncIterator[ServerSentEvent]:
        async with (
            session_factory() as stream_session,
            aclosing(
                rag_service.answer(
                    stream_session,
                    qdrant,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    question=body.content,
                    model_id=body.model_id,
                )
            ) as chat_events,
        ):
            async for event in chat_events:
                yield ServerSentEvent(
                    data=json.dumps(event.data, ensure_ascii=False), event=event.event
                )

    return EventSourceResponse(
        events(),
        ping=SSE_PING_S,
        # No buffering by nginx (deployment), no caching anywhere.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
