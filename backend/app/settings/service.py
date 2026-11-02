"""Settings business logic: encrypted API keys per provider (T05), default model (T06)."""

import logging
import uuid

from fastapi import status
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.core.errors import AppError
from app.db.models import Provider, UserApiKey, UserSettings
from app.llm import client as llm_client
from app.llm.errors import USER_MESSAGES, LLMError, LLMErrorCode
from app.settings.schemas import ApiKeyOut, SettingsOut

logger = logging.getLogger(__name__)


async def get_settings(session: AsyncSession, user_id: uuid.UUID) -> SettingsOut:
    user_settings = await session.get(UserSettings, user_id)
    keys = await session.scalars(
        select(UserApiKey).where(UserApiKey.user_id == user_id).order_by(UserApiKey.provider)
    )
    return SettingsOut(
        default_provider=user_settings.default_provider if user_settings else None,
        default_model_id=user_settings.default_model_id if user_settings else None,
        api_keys=[ApiKeyOut.model_validate(key) for key in keys],
    )


# invalid_key is the user's fault; a throttled provider is temporary; anything else is an
# upstream failure. In every case the key is not saved.
_CHECK_STATUS: dict[LLMErrorCode, int] = {
    "invalid_key": status.HTTP_400_BAD_REQUEST,
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
