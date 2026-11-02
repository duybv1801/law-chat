import os

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import BACKEND_DIR, Settings, export_process_env
from scripts.gen_keys import generate

KEY_1 = Fernet.generate_key().decode()
KEY_2 = Fernet.generate_key().decode()
JWT = "j" * 32
REQUIRED = {"APP_ENCRYPTION_KEYS": KEY_1, "JWT_SECRET": JWT}


def make(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    # Isolate from the developer's shell env and the test defaults in conftest.
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    for name, value in {**REQUIRED, **env}.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch)
    assert s.database_url.get_secret_value().startswith("postgresql+asyncpg://")
    assert s.embedding_backend == "bge"
    assert s.embedding_device == "auto"
    assert s.embedding_threads is None
    assert s.upload_dir == BACKEND_DIR / "storage/uploads"


def test_encryption_keys_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, APP_ENCRYPTION_KEYS=f" {KEY_1} , {KEY_2} ,")
    assert [k.get_secret_value() for k in s.app_encryption_keys] == [KEY_1, KEY_2]


def test_secrets_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt = "jwt-secret-value-" + "x" * 32
    s = make(monkeypatch, JWT_SECRET=jwt)
    assert KEY_1 not in repr(s)
    assert jwt not in repr(s)
    assert "law:law@" not in repr(s)  # DB password in DATABASE_URL


def test_empty_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, EMBEDDING_THREADS="", HF_HOME="")
    assert s.embedding_threads is None
    assert s.hf_home is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_ENCRYPTION_KEYS", ","),
        ("APP_ENCRYPTION_KEYS", "not-a-fernet-key"),
        ("APP_ENCRYPTION_KEYS", f"{Fernet.generate_key().decode()},bad"),
        ("JWT_SECRET", "too-short"),
        ("EMBEDDING_DEVICE", "tpu"),
        ("EMBEDDING_BACKEND", "openai"),
        ("CRAWLER_RATE_LIMIT_RPS", "2"),
        ("TZ_APP", "Mars/Olympus"),
    ],
)
def test_invalid_values(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    with pytest.raises(ValidationError):
        make(monkeypatch, **{name: value})


def test_missing_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("APP_ENCRYPTION_KEYS", KEY_1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_export_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, HF_HOME="hf-cache")  # relative -> resolved against backend/
    # setenv first so monkeypatch records the originals and restores them after the test,
    # including the values export_process_env writes.
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "")
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK")
    monkeypatch.setenv("HF_HOME", "hf-cache")  # raw relative value, as from the shell
    export_process_env(s)
    assert os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"
    assert os.environ["HF_HOME"] == str(BACKEND_DIR / "hf-cache")


def test_gen_keys() -> None:
    keys = generate()
    Fernet(keys["APP_ENCRYPTION_KEYS"].encode())  # valid Fernet key
    assert len(keys["JWT_SECRET"]) >= 64
    assert generate() != keys
