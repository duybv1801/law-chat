"""Settings API routes: API keys per provider (T05); model list and default model (T06)."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.auth.rate_limit import api_key_save_limiter, enforce
from app.core.deps import CurrentUser, SessionDep
from app.llm.registry import ModelRegistry, registry_dep
from app.settings import service
from app.settings.schemas import (
    ApiKeyIn,
    ApiKeySaved,
    ModelOut,
    Provider,
    SettingsIn,
    SettingsOut,
)

router = APIRouter(prefix="/settings", tags=["settings"])
models_router = APIRouter(prefix="/models", tags=["settings"])

RegistryDep = Annotated[ModelRegistry, Depends(registry_dep)]


@models_router.get("")
async def list_models(_user: CurrentUser, registry: RegistryDep) -> list[ModelOut]:
    return [ModelOut.model_validate(m) for m in registry.enabled_models()]


@router.get("")
async def get_settings(
    user: CurrentUser, session: SessionDep, registry: RegistryDep
) -> SettingsOut:
    return await service.get_settings(session, user.id, registry)


@router.put("")
async def put_settings(
    body: SettingsIn, user: CurrentUser, session: SessionDep, registry: RegistryDep
) -> SettingsOut:
    return await service.set_default_model(session, user.id, body.default_model_id, registry)


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
