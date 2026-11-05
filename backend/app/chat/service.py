"""Conversation CRUD. Answering a message is `app.rag.service.answer`."""

import uuid

from fastapi import status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.schemas import PAGE_SIZE, ConversationList, ConversationOut, MessageOut
from app.core.errors import AppError
from app.db.models import Conversation, Message
from app.rag.service import DEFAULT_TITLE


def not_found() -> AppError:
    return AppError(status.HTTP_404_NOT_FOUND, "not_found", "Không tìm thấy cuộc hội thoại.")


async def get_conversation(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation:
    """The user's conversation; someone else's looks the same as a missing one (404)."""
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    if conversation is None:
        raise not_found()
    return conversation


async def create_conversation(session: AsyncSession, user_id: uuid.UUID) -> ConversationOut:
    conversation = Conversation(user_id=user_id, title=DEFAULT_TITLE)
    session.add(conversation)
    await session.flush()
    await session.refresh(conversation)
    await session.commit()
    return ConversationOut.model_validate(conversation)


async def list_conversations(
    session: AsyncSession, user_id: uuid.UUID, page: int
) -> ConversationList:
    """Most recently active first (a new message bumps `updated_at`, DB trigger)."""
    total = await session.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
    )
    rows = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    return ConversationList(
        items=[ConversationOut.model_validate(c) for c in rows], total=total or 0
    )


async def list_messages(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> list[MessageOut]:
    await get_conversation(session, user_id, conversation_id)
    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return [MessageOut.model_validate(m) for m in rows]


async def rename_conversation(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID, title: str
) -> ConversationOut:
    conversation = await get_conversation(session, user_id, conversation_id)
    conversation.title = title
    await session.flush()
    await session.refresh(conversation)
    await session.commit()
    return ConversationOut.model_validate(conversation)


async def delete_conversation(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> None:
    result = await session.execute(
        delete(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise not_found()
    await session.commit()
