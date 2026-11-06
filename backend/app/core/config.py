"""Application settings loaded from environment / `.env` (repo root).

Import this module before anything that imports torch or huggingface: it exports
process-level variables (`PYTORCH_ENABLE_MPS_FALLBACK`, `HF_HOME`) that those libraries
only read at import time.
"""

import os
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.crypto import build_fernet

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # `EMBEDDING_THREADS=` means "not set"
        extra="ignore",
        hide_input_in_errors=True,  # validation errors must never echo secrets
    )

    # SecretStr: the URL carries the DB password. Use `.get_secret_value()` at the call site.
    database_url: SecretStr = SecretStr("postgresql+asyncpg://law:law@localhost:5432/law")
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "legal_chunks"

    # MultiFernet: first key encrypts, the rest only decrypt (key rotation).
    app_encryption_keys: Annotated[list[SecretStr], NoDecode] = Field(min_length=1)
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_access_ttl_min: int = Field(default=30, gt=0)
    jwt_refresh_ttl_days: int = Field(default=14, gt=0)
    cookie_secure: bool = False

    embedding_backend: Literal["bge", "fake"] = "bge"
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: Literal["auto", "cuda", "mps", "cpu"] = "auto"
    embedding_batch_size: int = Field(default=16, gt=0)
    embedding_threads: int | None = Field(default=None, gt=0)
    pytorch_enable_mps_fallback: bool = True
    hf_home: Path | None = None

    upload_dir: Path = Path("storage/uploads")

    rag_top_k: int = Field(default=8, gt=0)
    # Tuned by the T25 eval (backend/eval/report.md, bge-m3 dense cosine).
    rag_min_score: float = 0.6

    # vbpl.vn politeness: never more than 1 request/second.
    crawler_rate_limit_rps: float = Field(default=1.0, gt=0, le=1)
    crawler_user_agent: str = "LawChatbot/0.1 (+contact email)"
    # Dev only: keep raw responses so parser changes do not re-download pages.
    crawler_cache: bool = False
    crawler_cache_dir: Path = Path("storage/crawl_cache")
    scheduler_enabled: bool = False
    # Standard 5-field crontab, in TZ_APP. Set `* * * * *` to check the scheduler quickly.
    scheduler_daily_new_cron: str = "0 2 * * *"
    scheduler_status_refresh_cron: str = "0 3 * * *"
    scheduler_status_refresh_batch_size: int = Field(default=500, gt=0)
    tz_app: str = "Asia/Ho_Chi_Minh"

    @field_validator("app_encryption_keys", mode="before")
    @classmethod
    def _split_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("app_encryption_keys")
    @classmethod
    def _check_fernet_keys(cls, value: list[SecretStr]) -> list[SecretStr]:
        build_fernet(value)  # the error names the key's position, never the key itself
        return value

    @field_validator("upload_dir", "hf_home", "crawler_cache_dir")
    @classmethod
    def _resolve_path(cls, value: Path | None) -> Path | None:
        # Relative paths are anchored at backend/ so the cwd does not matter.
        if value is None or value.is_absolute():
            return value
        return BACKEND_DIR / value

    @field_validator("scheduler_daily_new_cron", "scheduler_status_refresh_cron")
    @classmethod
    def _check_cron(cls, value: str) -> str:
        from apscheduler.triggers.cron import CronTrigger

        CronTrigger.from_crontab(value)  # ValueError on a bad expression
        return value

    @field_validator("tz_app")
    @classmethod
    def _check_tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone: {value}") from exc
        return value


def export_process_env(s: Settings) -> None:
    """Expose settings that torch / huggingface / LiteLLM read from `os.environ` at import time."""
    # LiteLLM fetches its model cost map from GitHub at import (5s timeout offline): use the
    # copy shipped with the package.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    os.environ.setdefault(
        "PYTORCH_ENABLE_MPS_FALLBACK", "1" if s.pytorch_enable_mps_fallback else "0"
    )
    if s.hf_home is not None:
        # Assign (not setdefault): a relative HF_HOME from the shell must be replaced by the
        # resolved absolute path, otherwise HF would resolve it against the cwd.
        os.environ["HF_HOME"] = str(s.hf_home)


settings = Settings()
export_process_env(settings)
