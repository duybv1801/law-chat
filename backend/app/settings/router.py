"""Settings API routes: API keys per provider (T05); default model (T06)."""

from fastapi import APIRouter, status

from app.auth.rate_limit import api_key_save_limiter, enforce
from app.core.deps import CurrentUser, SessionDep
from app.settings import service
from app.settings.schemas import ApiKeyIn, ApiKeySaved, Provider, SettingsOut

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings(user: CurrentUser, session: SessionDep) -> SettingsOut:
    return await service.get_settings(session, user.id)


@router.put("/api-keys/{provider}")
async def put_api_key(
    provider: Provider, body: ApiKeyIn, user: CurrentUser, session: SessionDep
) -> ApiKeySaved:
    enforce(api_key_save_limiter, str(user.id), record=True)
    key = await service.save_api_key(session, user.id, provider, body.api_key.get_secret_value())
    return ApiKeySaved.model_validate(key)


@router.delete("/api-keys/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(provider: Provider, user: CurrentUser, session: SessionDep) -> None:
    await service.delete_api_key(session, user.id, provider)
