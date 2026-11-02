import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.db.models import (
    Base,
    Conversation,
    LegalChunk,
    LegalDocument,
    Message,
    User,
    UserApiKey,
    UserSettings,
)


def _user(email: str = "a@example.com") -> User:
    return User(email=email, password_hash="argon2-hash")


def _vbpl_doc(**overrides: object) -> LegalDocument:
    values: dict[str, object] = {
        "source": "vbpl",
        "source_id": "12345",
        "so_hieu": "45/2019/QH14",
        "so_hieu_norm": "45/2019/QH14",
        "title": "Bộ luật Lao động",
        "content_hash": "abc",
    }
    values.update(overrides)
    return LegalDocument(**values)


def test_migration_roundtrip(alembic_config, migrated_db) -> None:
    # `migrated_db` only upgrades from an empty schema; this also exercises downgrade.
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


async def test_models_match_migration(db_session: AsyncSession) -> None:
    conn = await db_session.connection()
    diff = await conn.run_sync(
        lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
    )
    assert diff == []
    tables = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
    assert set(Base.metadata.tables) <= tables
    extensions = set((await conn.execute(text("SELECT extname FROM pg_extension"))).scalars())
    assert {"citext", "unaccent"} <= extensions


async def test_create_user_and_document(db_session: AsyncSession) -> None:
    user = _user()
    db_session.add(user)
    await db_session.flush()

    upload = LegalDocument(source="user_upload", owner_id=user.id, title="Hợp đồng mẫu")
    vbpl = _vbpl_doc()
    vbpl.chunks.append(
        LegalChunk(
            ordinal=0, dieu="Điều 1", text="Phạm vi", embed_text="Điều 1\nPhạm vi", token_count=3
        )
    )
    db_session.add_all([upload, vbpl])
    await db_session.commit()

    stored_user = await db_session.scalar(select(User).where(User.email == "A@Example.com"))
    assert stored_user is not None and stored_user.id == user.id  # citext: case-insensitive
    assert stored_user.role == "user" and stored_user.is_active
    assert stored_user.created_at.tzinfo is not None

    await db_session.refresh(upload)
    assert upload.status == "khong_xac_dinh"
    assert upload.ingest_status == "pending"
    assert upload.scope == "trung_uong"
    assert upload.replaced_by == [] and upload.amended_by == []
    assert upload.content_hash is None

    count = await db_session.scalar(
        select(func.count()).select_from(LegalChunk).where(LegalChunk.document_id == vbpl.id)
    )
    assert count == 1


async def test_email_unique_case_insensitive(db_session: AsyncSession) -> None:
    db_session.add(_user("x@example.com"))
    await db_session.flush()
    db_session.add(_user("X@EXAMPLE.COM"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.parametrize(
    "document",
    [
        lambda: LegalDocument(source="user_upload", title="no owner"),  # upload needs owner
        lambda: _vbpl_doc(status="valid"),
        lambda: _vbpl_doc(source="other"),
        lambda: _vbpl_doc(scope="global"),
        lambda: _vbpl_doc(ingest_status="queued"),
    ],
)
async def test_document_check_constraints(db_session: AsyncSession, document) -> None:
    db_session.add(document())
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_user_role_check(db_session: AsyncSession) -> None:
    db_session.add(User(email="r@example.com", password_hash="h", role="root"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_source_id_unique_only_when_set(db_session: AsyncSession) -> None:
    db_session.add_all([_vbpl_doc(source_id=None), _vbpl_doc(source_id=None)])
    await db_session.flush()  # NULL source_id: no uniqueness
    db_session.add_all([_vbpl_doc(source_id="1"), _vbpl_doc(source_id="1")])
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_api_key_unique_per_provider(db_session: AsyncSession) -> None:
    user = _user()
    db_session.add(user)
    await db_session.flush()
    for _ in range(2):
        db_session.add(
            UserApiKey(user_id=user.id, provider="openai", encrypted_key=b"t", key_last4="abcd")
        )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_delete_user_cascades_keys_and_settings(db_session: AsyncSession) -> None:
    user = _user()
    db_session.add(user)
    await db_session.flush()
    db_session.add_all(
        [
            UserSettings(user_id=user.id, default_provider="openai"),
            UserApiKey(user_id=user.id, provider="gemini", encrypted_key=b"t", key_last4="wxyz"),
        ]
    )
    await db_session.flush()

    # Raw SQL delete: proves the FK ON DELETE CASCADE, not just the ORM cascade.
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    db_session.expunge_all()
    assert await db_session.scalar(select(UserApiKey).where(UserApiKey.user_id == user.id)) is None
    assert await db_session.get(UserSettings, user.id) is None


async def test_delete_document_cascades_chunks(db_session: AsyncSession) -> None:
    doc = _vbpl_doc()
    chunk_id = uuid.uuid4()
    doc.chunks.append(LegalChunk(id=chunk_id, ordinal=0, text="t", embed_text="t", token_count=1))
    db_session.add(doc)
    await db_session.flush()

    await db_session.execute(text("DELETE FROM legal_documents WHERE id = :id"), {"id": doc.id})
    db_session.expunge_all()
    assert await db_session.get(LegalChunk, chunk_id) is None


async def test_messages_keep_insert_order_in_one_transaction(db_session: AsyncSession) -> None:
    user = _user()
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="t")
    db_session.add(conversation)
    await db_session.flush()
    for content in ("question", "answer"):
        db_session.add(Message(conversation_id=conversation.id, role="user", content=content))
        await db_session.flush()

    rows = await db_session.scalars(
        select(Message.content)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
    )
    assert list(rows) == ["question", "answer"]


async def test_new_message_bumps_conversation_updated_at(db_session: AsyncSession) -> None:
    user = _user()
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="t")
    db_session.add(conversation)
    await db_session.flush()
    await db_session.refresh(conversation)
    before = conversation.updated_at

    message = Message(conversation_id=conversation.id, role="user", content="hi")
    db_session.add(message)
    await db_session.flush()
    await db_session.refresh(conversation)
    await db_session.refresh(message)
    assert conversation.updated_at == message.created_at > before
