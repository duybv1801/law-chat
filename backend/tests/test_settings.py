import logging
import uuid

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rate_limit import API_KEY_SAVE_MAX_ATTEMPTS
from app.core.crypto import decrypt_secret
from app.db.models import PROVIDER_LABELS, PROVIDERS, User, UserApiKey, UserSettings
from app.llm import client as llm_client
from app.llm.errors import LLMError
from app.llm.registry import ModelRegistry, registry_dep
from app.settings.schemas import ApiKeyIn

PASSWORD = "mat-khau-123"
FAKE_KEY = "sk-fake-0123456789abcdefWXYZ"
OTHER_KEY = "sk-fake-another-key-9876"


@pytest.fixture
def validator_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Mock `validate_key` (the real one calls the provider, see tests/test_llm_client.py).

    Keys containing "bad" are rejected, "flaky" is rate limited, "boom" raises an unmapped
    exception, "unchecked" passes without a provider check; any other key is confirmed.
    """
    calls: list[tuple[str, str]] = []

    async def fake_validate_key(*, provider: str, api_key: str) -> bool:
        calls.append((provider, api_key))
        if "bad" in api_key:
            raise LLMError("invalid_key")
        if "flaky" in api_key:
            raise LLMError("rate_limited")
        if "boom" in api_key:
            raise RuntimeError(f"connect timeout, request had api_key={api_key}")
        return "unchecked" not in api_key

    monkeypatch.setattr(llm_client, "validate_key", fake_validate_key)
    return calls


async def _login(client: httpx.AsyncClient, email: str = "an@example.com") -> uuid.UUID:
    response = await client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


async def _put_key(client: httpx.AsyncClient, provider: str, key: str) -> httpx.Response:
    return await client.put(f"/api/settings/api-keys/{provider}", json={"api_key": key})


# --- pure logic ---------------------------------------------------------------------------


def test_api_key_schema_hides_and_strips_key() -> None:
    body = ApiKeyIn.model_validate({"api_key": f"  {FAKE_KEY}\n"})
    assert body.api_key.get_secret_value() == FAKE_KEY
    assert FAKE_KEY not in repr(body) and FAKE_KEY not in str(body)


async def test_validate_key_rejects_blank_or_spaced_without_provider_call() -> None:
    for bad in ("", "sk with space"):
        with pytest.raises(LLMError) as info:
            await llm_client.validate_key(provider="openai", api_key=bad)
        assert info.value.code == "invalid_key"


# --- API ----------------------------------------------------------------------------------


async def test_settings_requires_login(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/settings")).status_code == 401
    assert (await _put_key(client, "openai", FAKE_KEY)).status_code == 401
    assert (await client.delete("/api/settings/api-keys/openai")).status_code == 401


async def test_get_settings_empty(client: httpx.AsyncClient) -> None:
    await _login(client)
    response = await client.get("/api/settings")
    assert response.status_code == 200
    assert response.json() == {"default_provider": None, "default_model_id": None, "api_keys": []}


async def test_put_key_stores_encrypted_and_returns_last4(
    client: httpx.AsyncClient, db_session: AsyncSession, validator_calls
) -> None:
    user_id = await _login(client)
    response = await _put_key(client, "openai", f" {FAKE_KEY} ")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"provider", "key_last4", "validated_at"}
    assert body["provider"] == "openai"
    assert body["key_last4"] == "WXYZ"
    assert body["validated_at"] is not None
    assert validator_calls == [("openai", FAKE_KEY)]  # validated with the stripped key

    row = await db_session.scalar(select(UserApiKey).where(UserApiKey.user_id == user_id))
    assert row is not None
    assert FAKE_KEY.encode() not in row.encrypted_key
    assert decrypt_secret(row.encrypted_key) == FAKE_KEY


async def test_get_settings_only_exposes_last4(client: httpx.AsyncClient, validator_calls) -> None:
    await _login(client)
    await _put_key(client, "openai", FAKE_KEY)
    await _put_key(client, "anthropic", OTHER_KEY)

    response = await client.get("/api/settings")
    assert response.status_code == 200
    assert FAKE_KEY not in response.text and OTHER_KEY not in response.text
    keys = response.json()["api_keys"]
    assert [k["provider"] for k in keys] == ["anthropic", "openai"]
    for key in keys:
        assert set(key) == {"provider", "key_last4", "validated_at", "last_used_at"}
    assert {k["provider"]: k["key_last4"] for k in keys} == {"anthropic": "9876", "openai": "WXYZ"}


async def test_put_key_replaces_existing(
    client: httpx.AsyncClient, db_session: AsyncSession, validator_calls
) -> None:
    user_id = await _login(client)
    await _put_key(client, "gemini", FAKE_KEY)
    first = await db_session.scalar(select(UserApiKey).where(UserApiKey.user_id == user_id))
    assert first is not None
    first_id = first.id

    response = await _put_key(client, "gemini", OTHER_KEY)
    assert response.status_code == 200
    assert response.json()["key_last4"] == "9876"

    rows = (await db_session.scalars(select(UserApiKey).where(UserApiKey.user_id == user_id))).all()
    assert len(rows) == 1
    assert rows[0].id == first_id
    assert decrypt_secret(rows[0].encrypted_key) == OTHER_KEY


async def test_invalid_key_is_rejected_and_not_saved(
    client: httpx.AsyncClient, db_session: AsyncSession, validator_calls
) -> None:
    user_id = await _login(client)
    await _put_key(client, "openai", FAKE_KEY)

    response = await _put_key(client, "openai", "sk-bad-key-0000")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_key"
    assert "sk-bad-key-0000" not in response.text

    # The previous valid key is kept untouched.
    row = await db_session.scalar(select(UserApiKey).where(UserApiKey.user_id == user_id))
    assert row is not None
    assert decrypt_secret(row.encrypted_key) == FAKE_KEY


async def test_unverifiable_key_is_not_saved(
    client: httpx.AsyncClient, db_session: AsyncSession, validator_calls
) -> None:
    user_id = await _login(client)
    response = await _put_key(client, "anthropic", "sk-flaky-key-1111")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "rate_limited"
    assert await db_session.scalar(select(UserApiKey).where(UserApiKey.user_id == user_id)) is None


async def test_unexpected_check_error_is_502_without_leaking_key(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    validator_calls,
) -> None:
    caplog.set_level(logging.DEBUG)
    user_id = await _login(client)
    key = "sk-boom-key-2222"
    response = await _put_key(client, "openai", key)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_error"
    assert key not in response.text
    assert key not in caplog.text
    assert await db_session.scalar(select(UserApiKey).where(UserApiKey.user_id == user_id)) is None


async def test_unchecked_key_has_no_validated_at(
    client: httpx.AsyncClient, validator_calls
) -> None:
    await _login(client)
    await _put_key(client, "openai", FAKE_KEY)
    response = await _put_key(client, "openai", "sk-unchecked-3333")
    assert response.status_code == 200
    assert response.json()["validated_at"] is None  # replacing a key resets it


async def test_put_key_is_rate_limited_per_user(client: httpx.AsyncClient, validator_calls) -> None:
    await _login(client, "a@example.com")
    for _ in range(API_KEY_SAVE_MAX_ATTEMPTS):
        assert (await _put_key(client, "openai", FAKE_KEY)).status_code == 200
    response = await _put_key(client, "openai", FAKE_KEY)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_requests"
    assert int(response.headers["Retry-After"]) > 0
    assert len(validator_calls) == API_KEY_SAVE_MAX_ATTEMPTS  # no provider call once limited

    client.cookies.clear()
    await _login(client, "b@example.com")
    assert (await _put_key(client, "openai", FAKE_KEY)).status_code == 200


@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("mistral", {"api_key": FAKE_KEY}),  # unknown provider
        ("openai", {"api_key": "short"}),
        ("openai", {"api_key": "x" * 513}),
        ("openai", {}),
    ],
)
async def test_put_key_validation_errors(
    client: httpx.AsyncClient, validator_calls, provider: str, payload: dict
) -> None:
    await _login(client)
    response = await client.put(f"/api/settings/api-keys/{provider}", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert FAKE_KEY not in response.text
    assert validator_calls == []


async def test_delete_key(client: httpx.AsyncClient, validator_calls) -> None:
    await _login(client)
    await _put_key(client, "openai", FAKE_KEY)
    await _put_key(client, "gemini", OTHER_KEY)

    response = await client.delete("/api/settings/api-keys/openai")
    assert response.status_code == 204
    keys = (await client.get("/api/settings")).json()["api_keys"]
    assert [k["provider"] for k in keys] == ["gemini"]

    # Idempotent.
    assert (await client.delete("/api/settings/api-keys/openai")).status_code == 204


async def test_delete_key_clears_default_on_that_provider(
    client: httpx.AsyncClient, db_session: AsyncSession, test_registry, validator_calls
) -> None:
    user_id = await _login(client)
    await _put_key(client, "openai", FAKE_KEY)
    await _put_key(client, "gemini", OTHER_KEY)
    db_session.add(
        UserSettings(user_id=user_id, default_provider="openai", default_model_id="openai-main")
    )
    await db_session.flush()

    await client.delete("/api/settings/api-keys/gemini")
    body = (await client.get("/api/settings")).json()
    assert (body["default_provider"], body["default_model_id"]) == ("openai", "openai-main")

    await client.delete("/api/settings/api-keys/openai")
    body = (await client.get("/api/settings")).json()
    assert (body["default_provider"], body["default_model_id"]) == (None, None)


async def test_keys_are_per_user(client: httpx.AsyncClient, validator_calls) -> None:
    await _login(client, "a@example.com")
    await _put_key(client, "openai", FAKE_KEY)
    client.cookies.clear()

    await _login(client, "b@example.com")
    assert (await client.get("/api/settings")).json()["api_keys"] == []
    # B deleting "their" openai key must not touch A's.
    assert (await client.delete("/api/settings/api-keys/openai")).status_code == 204
    client.cookies.clear()

    await client.post("/api/auth/login", json={"email": "a@example.com", "password": PASSWORD})
    keys = (await client.get("/api/settings")).json()["api_keys"]
    assert [k["key_last4"] for k in keys] == ["WXYZ"]


async def test_deleting_user_deletes_keys(
    client: httpx.AsyncClient, db_session: AsyncSession, validator_calls
) -> None:
    user_id = await _login(client)
    await _put_key(client, "openai", FAKE_KEY)
    await _put_key(client, "anthropic", OTHER_KEY)

    await db_session.execute(delete(User).where(User.id == user_id))
    remaining = await db_session.scalars(select(UserApiKey).where(UserApiKey.user_id == user_id))
    assert remaining.all() == []


async def test_key_never_logged(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture, validator_calls
) -> None:
    caplog.set_level(logging.DEBUG)  # every logger, including sqlalchemy / httpx
    await _login(client)
    await _put_key(client, "openai", FAKE_KEY)
    await _put_key(client, "openai", "sk-bad-key-0000")
    await _put_key(client, "openai", "short")
    await client.get("/api/settings")
    await client.delete("/api/settings/api-keys/openai")

    assert caplog.records, "expected some log output to inspect"
    for secret in (FAKE_KEY, "sk-bad-key-0000"):
        assert secret not in caplog.text
        for record in caplog.records:
            assert secret not in repr(record.args)


# --- models & default model (T06) ---------------------------------------------------------

TEST_REGISTRY = ModelRegistry.model_validate(
    {
        "models": [
            {
                "id": "openai-main",
                "provider": "openai",
                "litellm_model": "openai/secret-name",
                "label": "ChatGPT",
                "max_context_tokens": 128000,
            },
            {
                "id": "anthropic-main",
                "provider": "anthropic",
                "litellm_model": "anthropic/secret-name",
                "label": "Claude",
                "max_context_tokens": 200000,
            },
            {
                "id": "openai-old",
                "provider": "openai",
                "litellm_model": "openai/old-name",
                "label": "Old",
                "max_context_tokens": 8000,
                "enabled": False,
            },
        ],
        "validation_model": {
            "openai": "openai/c",
            "anthropic": "anthropic/c",
            "gemini": "gemini/c",
        },
    }
)


@pytest.fixture
def test_registry(client: httpx.AsyncClient):
    from app.main import app

    app.dependency_overrides[registry_dep] = lambda: TEST_REGISTRY
    yield TEST_REGISTRY
    app.dependency_overrides.pop(registry_dep, None)


async def _put_settings(client: httpx.AsyncClient, model_id: str | None) -> httpx.Response:
    return await client.put("/api/settings", json={"default_model_id": model_id})


async def test_models_requires_login(client: httpx.AsyncClient, test_registry) -> None:
    assert (await client.get("/api/models")).status_code == 401
    assert (await _put_settings(client, "openai-main")).status_code == 401


async def test_list_models_only_enabled_without_litellm_name(
    client: httpx.AsyncClient, test_registry
) -> None:
    await _login(client)
    response = await client.get("/api/models")
    assert response.status_code == 200
    assert response.json() == [
        {"id": "openai-main", "provider": "openai", "label": "ChatGPT"},
        {"id": "anthropic-main", "provider": "anthropic", "label": "Claude"},
    ]
    assert "secret-name" not in response.text


async def test_list_models_from_repo_yaml(client: httpx.AsyncClient) -> None:
    await _login(client)
    response = await client.get("/api/models")
    assert response.status_code == 200
    assert {m["provider"] for m in response.json()} == {"openai", "anthropic", "gemini"}
    assert "litellm" not in response.text and "<model-id>" not in response.text


async def test_put_settings_sets_model_and_provider(
    client: httpx.AsyncClient, db_session: AsyncSession, test_registry, validator_calls
) -> None:
    user_id = await _login(client)
    await _put_key(client, "openai", FAKE_KEY)
    await _put_key(client, "anthropic", OTHER_KEY)

    response = await _put_settings(client, "openai-main")
    assert response.status_code == 200
    body = response.json()
    assert (body["default_provider"], body["default_model_id"]) == ("openai", "openai-main")
    assert [k["provider"] for k in body["api_keys"]] == ["anthropic", "openai"]

    # Switching updates the same row; provider follows the model.
    response = await _put_settings(client, "anthropic-main")
    assert response.status_code == 200
    assert response.json()["default_provider"] == "anthropic"
    rows = (
        await db_session.scalars(select(UserSettings).where(UserSettings.user_id == user_id))
    ).all()
    assert [(r.default_provider, r.default_model_id) for r in rows] == [
        ("anthropic", "anthropic-main")
    ]
    assert (await client.get("/api/settings")).json()["default_model_id"] == "anthropic-main"


async def test_put_settings_null_clears_default(
    client: httpx.AsyncClient, test_registry, validator_calls
) -> None:
    await _login(client)
    # Clearing works even before any settings row exists.
    response = await _put_settings(client, None)
    assert response.status_code == 200
    assert (response.json()["default_provider"], response.json()["default_model_id"]) == (
        None,
        None,
    )

    await _put_key(client, "openai", FAKE_KEY)
    await _put_settings(client, "openai-main")
    body = (await _put_settings(client, None)).json()
    assert (body["default_provider"], body["default_model_id"]) == (None, None)


async def test_put_settings_requires_key_for_provider(
    client: httpx.AsyncClient, test_registry, validator_calls
) -> None:
    await _login(client)
    await _put_key(client, "openai", FAKE_KEY)
    await _put_settings(client, "openai-main")

    response = await _put_settings(client, "anthropic-main")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "no_api_key"
    assert "Anthropic" in response.json()["error"]["message"]
    # Previous default is kept.
    assert (await client.get("/api/settings")).json()["default_model_id"] == "openai-main"


async def test_put_settings_key_of_other_user_does_not_count(
    client: httpx.AsyncClient, test_registry, validator_calls
) -> None:
    await _login(client, "a@example.com")
    await _put_key(client, "openai", FAKE_KEY)
    client.cookies.clear()

    await _login(client, "b@example.com")
    response = await _put_settings(client, "openai-main")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "no_api_key"


@pytest.mark.parametrize("model_id", ["openai-old", "does-not-exist", "openai/secret-name"])
async def test_put_settings_rejects_unknown_or_disabled_model(
    client: httpx.AsyncClient, test_registry, validator_calls, model_id: str
) -> None:
    await _login(client)
    await _put_key(client, "openai", FAKE_KEY)
    response = await _put_settings(client, model_id)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_model"
    assert (await client.get("/api/settings")).json()["default_model_id"] is None


@pytest.mark.parametrize("payload", [{}, {"default_model_id": 5}, {"default_model_id": "x" * 65}])
async def test_put_settings_validation_errors(
    client: httpx.AsyncClient, test_registry, payload: dict
) -> None:
    await _login(client)
    response = await client.put("/api/settings", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_deleting_key_after_put_settings_clears_default(
    client: httpx.AsyncClient, test_registry, validator_calls
) -> None:
    await _login(client)
    await _put_key(client, "openai", FAKE_KEY)
    await _put_settings(client, "openai-main")
    await client.delete("/api/settings/api-keys/openai")
    body = (await client.get("/api/settings")).json()
    assert (body["default_provider"], body["default_model_id"]) == (None, None)


async def test_default_model_disabled_later_reads_as_null(
    client: httpx.AsyncClient, db_session: AsyncSession, test_registry, validator_calls
) -> None:
    from app.main import app

    user_id = await _login(client)
    await _put_key(client, "openai", FAKE_KEY)
    await _put_settings(client, "openai-main")

    disabled = TEST_REGISTRY.model_copy(
        update={
            "models": [
                m.model_copy(update={"enabled": m.id != "openai-main"})
                for m in TEST_REGISTRY.models
            ]
        }
    )
    app.dependency_overrides[registry_dep] = lambda: disabled
    body = (await client.get("/api/settings")).json()
    assert (body["default_provider"], body["default_model_id"]) == (None, None)
    # The stored row is untouched: re-enabling the model brings the default back.
    row = await db_session.get(UserSettings, user_id)
    assert row.default_model_id == "openai-main"
    app.dependency_overrides[registry_dep] = lambda: TEST_REGISTRY
    assert (await client.get("/api/settings")).json()["default_model_id"] == "openai-main"


def test_provider_labels_cover_every_provider() -> None:
    assert set(PROVIDER_LABELS) == set(PROVIDERS)
