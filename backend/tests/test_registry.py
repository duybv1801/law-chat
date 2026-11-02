import logging
from pathlib import Path

import pytest
import yaml

from app.llm import registry as registry_module
from app.llm.registry import (
    MODEL_ID_MAX,
    MODELS_YAML,
    RegistryError,
    get_registry,
    load_registry,
)
from app.settings.schemas import SettingsIn

VALID = {
    "models": [
        {
            "id": "openai-main",
            "provider": "openai",
            "litellm_model": "openai/gpt-test",
            "label": "ChatGPT",
            "max_context_tokens": 128000,
            "enabled": True,
        },
        {
            "id": "gemini-old",
            "provider": "gemini",
            "litellm_model": "gemini/old",
            "label": "Gemini cũ",
            "max_context_tokens": 32000,
            "enabled": False,
        },
    ],
    "validation_model": {
        "openai": "openai/cheap",
        "anthropic": "anthropic/cheap",
        "gemini": "gemini/cheap",
    },
}


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data), encoding="utf-8")
    return path


def _with_model(**changes: object) -> dict:
    data = yaml.safe_load(yaml.safe_dump(VALID))  # deep copy
    data["models"][0].update(changes)
    return data


def _load_error(tmp_path: Path, data: object) -> str:
    with pytest.raises(RegistryError) as info:
        load_registry(_write(tmp_path, data))
    return str(info.value)


def test_load_valid(tmp_path: Path) -> None:
    registry = load_registry(_write(tmp_path, VALID))
    assert [m.id for m in registry.enabled_models()] == ["openai-main"]
    assert registry.get_enabled("openai-main").litellm_model == "openai/gpt-test"
    assert registry.get_enabled("gemini-old") is None  # disabled
    assert registry.get_enabled("nope") is None
    assert registry.validation_model["anthropic"] == "anthropic/cheap"
    assert registry.placeholders() == []


def test_enabled_defaults_to_true(tmp_path: Path) -> None:
    data = _with_model()
    del data["models"][0]["enabled"]
    assert load_registry(_write(tmp_path, data)).models[0].enabled is True


def test_repo_models_yaml_loads(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    registry = load_registry(MODELS_YAML)
    assert {m.provider for m in registry.enabled_models()} == {"openai", "anthropic", "gemini"}
    # Owner still has to fill in real model names: warned about, not fatal.
    if registry.placeholders():
        assert "placeholder" in caplog.text


def test_placeholders_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    registry = load_registry(_write(tmp_path, _with_model(litellm_model="openai/<model-id>")))
    assert registry.placeholders() == ["models[openai-main].litellm_model"]
    assert "models[openai-main].litellm_model" in caplog.text


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="file not found"):
        load_registry(tmp_path / "missing.yaml")


def test_unreadable_file_is_registry_error(tmp_path: Path) -> None:
    path = tmp_path / "models.yaml"
    path.write_bytes("label: Gemini cũ\n".encode("utf-16"))
    with pytest.raises(RegistryError, match="models.yaml: cannot read file"):
        load_registry(path)
    with pytest.raises(RegistryError, match="cannot read file"):
        load_registry(tmp_path)  # a directory


def test_invalid_yaml_names_file(tmp_path: Path) -> None:
    message = _load_error(tmp_path, "models: [\n  - id: x\n")
    assert "models.yaml" in message and "invalid YAML" in message


@pytest.mark.parametrize("content", ["", "- a\n- b\n", "just text\n"])
def test_not_a_mapping(tmp_path: Path, content: str) -> None:
    assert "expected a mapping" in _load_error(tmp_path, content)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_with_model(provider="mistral"), "models.0.provider"),
        (_with_model(litellm_model="anthropic/x"), "must start with 'openai/'"),
        (_with_model(max_context_tokens=0), "models.0.max_context_tokens"),
        (_with_model(label=""), "models.0.label"),
        (_with_model(id="Has Space"), "models.0.id"),
        (_with_model(temprature=0.1), "models.0.temprature"),  # typo -> extra field
        (_with_model(id="gemini-old"), "duplicate model id: gemini-old"),
        ({**VALID, "models": []}, "models"),
        ({"validation_model": VALID["validation_model"]}, "models: Field required"),
        ({"models": VALID["models"]}, "validation_model: Field required"),
        (
            {**VALID, "validation_model": {"openai": "openai/a", "gemini": "gemini/b"}},
            "validation_model is missing provider: anthropic",
        ),
        (
            {**VALID, "validation_model": {**VALID["validation_model"], "gemini": "openai/x"}},
            "validation_model.gemini must start with 'gemini/'",
        ),
        (_with_model(enabled=False), "at least one model must be enabled"),
    ],
)
def test_schema_errors_are_explicit(tmp_path: Path, data: dict, expected: str) -> None:
    message = _load_error(tmp_path, data)
    assert "invalid model registry" in message
    assert expected in message


def test_get_registry_is_cached() -> None:
    assert get_registry() is get_registry()


def test_longest_model_id_is_accepted_by_settings(tmp_path: Path) -> None:
    long_id = "a" * MODEL_ID_MAX
    registry = load_registry(_write(tmp_path, _with_model(id=long_id)))
    assert registry.get_enabled(long_id) is not None
    assert SettingsIn(default_model_id=long_id).default_model_id == long_id


async def test_app_refuses_to_start_on_broken_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import app, lifespan

    monkeypatch.setattr(registry_module, "MODELS_YAML", _write(tmp_path, "models: [\n"))
    get_registry.cache_clear()
    try:
        with pytest.raises(RegistryError, match="invalid YAML"):
            async with lifespan(app):
                pass
    finally:
        get_registry.cache_clear()
