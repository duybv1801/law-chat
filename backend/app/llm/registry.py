"""Model registry loaded from config/models.yaml (docs/05-rag-chat.md, "Model registry").

Model names live only in the YAML file (rule 7). The file is checked with Pydantic when the
backend starts (`get_registry()` in the lifespan); any problem raises `RegistryError` with
the file path and the offending fields, so the app refuses to start instead of failing later.
"""

import logging
from functools import cache
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.config import BACKEND_DIR
from app.db.models import PROVIDERS, Provider

MODELS_YAML = BACKEND_DIR / "config" / "models.yaml"

# Marks a value the owner still has to fill in ("openai/<model-id>").
PLACEHOLDER_MARK = "<"

logger = logging.getLogger(__name__)

NonEmptyStr = Annotated[str, Field(min_length=1)]

# Shared with SettingsIn so a listed model id is never rejected by PUT /settings.
MODEL_ID_MAX = 64


class RegistryError(Exception):
    """models.yaml is missing, is not valid YAML, or does not match the schema."""


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=MODEL_ID_MAX)]
    provider: Provider
    litellm_model: NonEmptyStr
    label: NonEmptyStr
    max_context_tokens: Annotated[int, Field(gt=0)]
    enabled: bool = True

    @model_validator(mode="after")
    def _prefix_matches_provider(self) -> Self:
        # The user's key for `provider` is what gets sent, so the model must belong to it.
        if not self.litellm_model.startswith(f"{self.provider}/"):
            raise ValueError(f"litellm_model must start with '{self.provider}/'")
        return self


class ModelRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    models: Annotated[list[ModelSpec], Field(min_length=1)]
    # Cheap model per provider used to check a key when it is saved (`client.validate_key`).
    validation_model: dict[Provider, NonEmptyStr]

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids = [m.id for m in self.models]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate model id: {', '.join(duplicates)}")
        if not any(m.enabled for m in self.models):
            raise ValueError("at least one model must be enabled")
        missing = [p for p in PROVIDERS if p not in self.validation_model]
        if missing:
            raise ValueError(f"validation_model is missing provider: {', '.join(missing)}")
        for provider, model in self.validation_model.items():
            if not model.startswith(f"{provider}/"):
                raise ValueError(f"validation_model.{provider} must start with '{provider}/'")
        return self

    def enabled_models(self) -> list[ModelSpec]:
        return [m for m in self.models if m.enabled]

    def get_enabled(self, model_id: str) -> ModelSpec | None:
        """The enabled model with this id, or None (unknown and disabled look the same)."""
        return next((m for m in self.models if m.id == model_id and m.enabled), None)

    def placeholders(self) -> list[str]:
        """Paths of values still holding a `<...>` placeholder."""
        values = {f"models[{m.id}].litellm_model": m.litellm_model for m in self.models}
        values |= {f"validation_model.{p}": v for p, v in self.validation_model.items()}
        return [path for path, value in values.items() if PLACEHOLDER_MARK in value]


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def load_registry(path: Path = MODELS_YAML) -> ModelRegistry:
    """Read and validate `path`. Raise `RegistryError` with a readable reason on any problem."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RegistryError(f"{path}: file not found") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise RegistryError(f"{path}: cannot read file: {exc}") from None
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path}: invalid YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise RegistryError(f"{path}: expected a mapping with 'models' and 'validation_model'")
    try:
        registry = ModelRegistry.model_validate(raw)
    except ValidationError as exc:
        raise RegistryError(f"{path}: invalid model registry:\n{_format_errors(exc)}") from None

    placeholders = registry.placeholders()
    if placeholders:
        # Not fatal: the app (settings, documents, ...) works; only LLM calls need real names.
        logger.warning("models.yaml still has placeholder model names: %s", ", ".join(placeholders))
    return registry


@cache
def get_registry() -> ModelRegistry:
    """The registry from config/models.yaml, loaded once per process."""
    return load_registry(MODELS_YAML)


async def registry_dep() -> ModelRegistry:
    """FastAPI dependency. Async so the cached lookup does not hop to the threadpool."""
    return get_registry()
