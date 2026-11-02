"""Settings request/response schemas. No schema ever carries a plaintext key outward."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.db.models import Provider

__all__ = ["ApiKeyIn", "ApiKeyOut", "ApiKeySaved", "Provider", "SettingsOut"]

API_KEY_MIN = 8
API_KEY_MAX = 512


class ApiKeyIn(BaseModel):
    # SecretStr: the key never shows up in repr() / logs of this object.
    api_key: SecretStr = Field(min_length=API_KEY_MIN, max_length=API_KEY_MAX)

    @field_validator("api_key", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ApiKeySaved(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: Provider
    key_last4: str
    validated_at: datetime | None


class ApiKeyOut(ApiKeySaved):
    last_used_at: datetime | None


class SettingsOut(BaseModel):
    default_provider: Provider | None
    default_model_id: str | None
    api_keys: list[ApiKeyOut]
