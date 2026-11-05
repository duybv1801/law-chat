"""Settings business logic: encrypted API keys per provider (T05), default model (T06)."""

import logging
import uuid

from fastapi import status
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.core.errors import AppError
from app.db.models import PROVIDER_LABELS, Provider, UserApiKey, UserSettings
from app.llm import client as llm_client
from app.llm.errors import USER_MESSAGES, LLMError, LLMErrorCode
from app.llm.registry import ModelRegistry
from app.settings.schemas import ApiKeyOut, SettingsOut

logger = logging.getLogger(__name__)


async def get_settings(
    session: AsyncSession, user_id: uuid.UUID, registry: ModelRegistry
) -> SettingsOut:
    """A saved default whose model was later disabled or removed from models.yaml reads as null."""
    user_settings = await session.get(UserSettings, user_id)
    keys = await session.scalars(
        select(UserApiKey).where(UserApiKey.user_id == user_id).order_by(UserApiKey.provider)
    )
    model_id = user_settings.default_model_id if user_settings else None
    if model_id is not None and registry.get_enabled(model_id) is None:
        model_id = None
    return SettingsOut(
        default_provider=user_settings.default_provider if model_id else None,
        default_model_id=model_id,
        api_keys=[ApiKeyOut.model_validate(key) for key in keys],
    )


async def set_default_model(
    session: AsyncSession, user_id: uuid.UUID, model_id: str | None, registry: ModelRegistry
) -> SettingsOut:
    """Set (or clear, with None) the default model; `default_provider` follows the model.

    Only enabled models of a provider the user already has a key for can be chosen.
    """
    provider: Provider | None = None
    if model_id is not None:
        spec = registry.get_enabled(model_id)
        if spec is None:
            raise AppError(
                status.HTTP_400_BAD_REQUEST,
                "unknown_model",
                "Model không tồn tại hoặc đã bị tắt.",
            )
        # FOR UPDATE: a concurrent delete_api_key waits for our commit (and then clears this
        # default), or commits first and we see no key; the default never outlives the key.
        has_key = await session.scalar(
            select(UserApiKey.id)
            .where(UserApiKey.user_id == user_id, UserApiKey.provider == spec.provider)
            .with_for_update()
        )
        if has_key is None:
            raise AppError(
                status.HTTP_400_BAD_REQUEST,
                "no_api_key",
                f"Bạn chưa nhập API key {PROVIDER_LABELS[spec.provider]}. "
                "Vào Cài đặt để thêm key trước khi chọn model này.",
            )
        provider = spec.provider

    values = {"default_provider": provider, "default_model_id": model_id}
    await session.execute(
        insert(UserSettings)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(
            index_elements=[UserSettings.user_id],
            # Core UPDATE: set updated_at by hand.
            set_={**values, "updated_at": func.clock_timestamp()},
        )
    )
    await session.commit()
    logger.info("default model set: user=%s model=%s", user_id, model_id)
    return await get_settings(session, user_id, registry)


# invalid_key / access_denied / quota_exceeded need action in the user's provider account; a
# throttled provider is temporary; anything else is an upstream (or validation_model config)
# failure. In every case the key is not saved.
_CHECK_STATUS: dict[LLMErrorCode, int] = {
    "invalid_key": status.HTTP_400_BAD_REQUEST,
    "access_denied": status.HTTP_400_BAD_REQUEST,
    "quota_exceeded": status.HTTP_400_BAD_REQUEST,
    "rate_limited": status.HTTP_503_SERVICE_UNAVAILABLE,
}


async def _check_key(provider: Provider, api_key: str) -> bool:
    """Return True if the provider confirmed the key, False if it was not checked."""
    try:
        return await llm_client.validate_key(provider=provider, api_key=api_key)
    except LLMError as exc:
        status_code = _CHECK_STATUS.get(exc.code, status.HTTP_502_BAD_GATEWAY)
        raise AppError(status_code, exc.code, exc.user_message) from None
    except Exception as exc:
        # Unmapped error: log only its type, its message/args may carry the request (and key).
        logger.warning("api key check failed: provider=%s error=%s", provider, type(exc).__name__)
        raise AppError(
            status.HTTP_502_BAD_GATEWAY, "provider_error", USER_MESSAGES["provider_error"]
        ) from None


async def save_api_key(
    session: AsyncSession, user_id: uuid.UUID, provider: Provider, api_key: str
) -> UserApiKey:
    """Validate, encrypt and upsert the user's key for `provider`. Invalid keys are not saved."""
    checked = await _check_key(provider, api_key)
    now = func.clock_timestamp()
    values = {
        "encrypted_key": encrypt_secret(api_key),
        "key_last4": api_key[-4:],
        "validated_at": now if checked else None,
    }
    stmt = (
        insert(UserApiKey)
        .values(user_id=user_id, provider=provider, **values)
        .on_conflict_do_update(
            index_elements=[UserApiKey.user_id, UserApiKey.provider],
            # Core UPDATE: set updated_at by hand; a new key has not been used yet.
            set_={**values, "last_used_at": None, "updated_at": now},
        )
        .returning(UserApiKey)
        .execution_options(populate_existing=True)
    )
    row = (await session.scalars(stmt)).one()
    await session.commit()
    logger.info("api key saved: user=%s provider=%s", user_id, provider)
    return row


async def delete_api_key(session: AsyncSession, user_id: uuid.UUID, provider: Provider) -> None:
    """Idempotent: deleting a key that does not exist is not an error.

    A default model on this provider can no longer be used, so the default is cleared too.
    """
    await session.execute(
        delete(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == provider)
    )
    await session.execute(
        update(UserSettings)
        .where(UserSettings.user_id == user_id, UserSettings.default_provider == provider)
        # Core UPDATE: set updated_at by hand.
        .values(default_provider=None, default_model_id=None, updated_at=func.clock_timestamp())
    )
    await session.commit()
    logger.info("api key deleted: user=%s provider=%s", user_id, provider)
