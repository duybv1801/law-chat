"""Chat API schemas (docs/06-api.md, "Chat")."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.llm.registry import MODEL_ID_MAX

PAGE_SIZE = 20
QUESTION_MAX_CHARS = 4000
TITLE_MAX_CHARS = 200


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationList(BaseModel):
    items: list[ConversationOut]
    total: int


class ConversationPatch(BaseModel):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_CHARS)
    ]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    provider: str | None
    model_id: str | None
    # Citation objects as sent in the `citations` event (app.rag.citations.Citation).
    citations: list[dict[str, Any]]
    refused: bool
    error: str | None
    created_at: datetime


class MessageIn(BaseModel):
    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=QUESTION_MAX_CHARS)
    ]
    model_id: str | None = Field(default=None, max_length=MODEL_ID_MAX)
