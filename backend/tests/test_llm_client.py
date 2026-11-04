"""LLM client with LiteLLM mocked: error mapping, streaming, key validation, no key leaks."""

import logging
import os
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.llm import client as llm_client
from app.llm.errors import USER_MESSAGES, LLMError
from app.llm.registry import ModelRegistry

# Through the client (not `import litellm`) so app.core.config runs first: no network at import.
litellm = llm_client._litellm()

KEY = "sk-secret-test-key-0123456789"

REGISTRY = ModelRegistry.model_validate(
    {
        "models": [
            {
                "id": "openai-main",
                "provider": "openai",
                "litellm_model": "openai/test-model",
                "label": "OpenAI",
                "max_context_tokens": 1000,
            },
            {
                "id": "gemini-off",
                "provider": "gemini",
                "litellm_model": "gemini/test-model",
                "label": "Gemini",
                "max_context_tokens": 1000,
                "enabled": False,
            },
        ],
        "validation_model": {
            "openai": "openai/cheap-model",
            "anthropic": "anthropic/<cheap-model-id>",
            "gemini": "gemini/cheap-model",
        },
    }
)


def _error(cls: type[Exception], status: int = 400) -> Exception:
    # The message echoes the key, as provider errors sometimes do.
    kwargs: dict[str, Any] = {"message": f"bad request with key {KEY}"}
    kwargs |= {"model": "openai/test-model", "llm_provider": "openai"}
    if cls is litellm.PermissionDeniedError:
        kwargs["response"] = httpx.Response(status, request=httpx.Request("POST", "https://x"))
    return cls(**kwargs)


class FakeStream:
    """Stands in for LiteLLM's CustomStreamWrapper."""

    def __init__(self, pieces: list[str | None], fail_after: Exception | None = None) -> None:
        self.pieces = pieces
        self.fail_after = fail_after
        self.closed = False

    async def __aiter__(self):
        yield SimpleNamespace(choices=[])  # e.g. a usage-only chunk
        for piece in self.pieces:
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece))])
        if self.fail_after is not None:
            raise self.fail_after

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """`litellm.acompletion` calls, recorded by `_patch_acompletion`; uses the test registry."""
    monkeypatch.setattr(llm_client, "get_registry", lambda: REGISTRY)
    return []


def _patch_acompletion(monkeypatch: pytest.MonkeyPatch, calls: list[dict], result: Any) -> None:
    async def fake_acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)


async def _collect(**kwargs: Any) -> list[str]:
    return [token async for token in llm_client.stream_chat(**kwargs)]


def _assert_no_leak(error: LLMError, caplog: pytest.LogCaptureFixture) -> None:
    assert KEY not in str(error) and KEY not in error.user_message
    # The provider exception (whose message has the key) is not chained into tracebacks.
    assert error.__cause__ is None and error.__suppress_context__
    assert KEY not in caplog.text


# --- map_exception ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (_error(litellm.AuthenticationError), "invalid_key"),
        (_error(litellm.PermissionDeniedError, 403), "invalid_key"),
        (_error(litellm.RateLimitError), "rate_limited"),
        (_error(litellm.ContextWindowExceededError), "context_too_long"),
        (_error(litellm.BadRequestError), "provider_error"),
        (_error(litellm.NotFoundError), "provider_error"),
        (_error(litellm.Timeout), "provider_error"),
        (_error(litellm.APIConnectionError), "provider_error"),
        (_error(litellm.ServiceUnavailableError), "provider_error"),
        (_error(litellm.InternalServerError), "provider_error"),
        (RuntimeError("anything else"), "provider_error"),
    ],
)
def test_map_exception(exc: Exception, code: str) -> None:
    error = llm_client.map_exception(exc)
    assert error.code == code
    assert error.user_message == USER_MESSAGES[code]
    assert KEY not in str(error)


def test_map_exception_keeps_llm_error() -> None:
    original = LLMError("rate_limited")
    assert llm_client.map_exception(original) is original


def test_litellm_logging_is_off() -> None:
    module = llm_client._litellm()
    assert module.turn_off_message_logging is True
    assert module.redact_messages_in_exceptions is True
    assert module.log_raw_request_response is False
    assert logging.getLogger("LiteLLM").getEffectiveLevel() >= logging.WARNING
    # Set by app.core.config so importing LiteLLM never hits the network.
    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"


# --- stream_chat --------------------------------------------------------------------------


async def test_stream_chat_yields_tokens(monkeypatch: pytest.MonkeyPatch, calls: list[dict]):
    stream = FakeStream(["Theo ", None, "", "Điều 5 [1]."])
    _patch_acompletion(monkeypatch, calls, stream)
    messages = [{"role": "user", "content": "Hỏi"}]

    tokens = await _collect(model_id="openai-main", api_key=KEY, messages=messages)

    assert tokens == ["Theo ", "Điều 5 [1]."]
    assert stream.closed
    [call] = calls
    assert call["model"] == "openai/test-model"
    assert call["api_key"] == KEY
    assert call["messages"] == messages
    assert call["stream"] is True
    assert call["temperature"] == llm_client.TEMPERATURE
    assert KEY not in os.environ.values()


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (_error(litellm.AuthenticationError), "invalid_key"),
        (_error(litellm.RateLimitError), "rate_limited"),
        (_error(litellm.ContextWindowExceededError), "context_too_long"),
        (_error(litellm.APIConnectionError), "provider_error"),
    ],
)
async def test_stream_chat_maps_errors(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict],
    caplog: pytest.LogCaptureFixture,
    exc: Exception,
    code: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    _patch_acompletion(monkeypatch, calls, exc)
    with pytest.raises(LLMError) as info:
        await _collect(model_id="openai-main", api_key=KEY, messages=[])
    assert info.value.code == code
    _assert_no_leak(info.value, caplog)
    assert f"code={code}" in caplog.text


async def test_stream_chat_error_mid_stream(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    stream = FakeStream(["Phần đầu "], fail_after=_error(litellm.RateLimitError))
    _patch_acompletion(monkeypatch, calls, stream)

    received: list[str] = []
    with pytest.raises(LLMError) as info:
        async for token in llm_client.stream_chat(model_id="openai-main", api_key=KEY, messages=[]):
            received.append(token)

    assert received == ["Phần đầu "]  # what was generated stays with the caller
    assert info.value.code == "rate_limited"
    assert stream.closed
    _assert_no_leak(info.value, caplog)


async def test_stream_chat_closed_early_closes_provider_stream(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict]
) -> None:
    stream = FakeStream(["a", "b", "c"])
    _patch_acompletion(monkeypatch, calls, stream)
    tokens = llm_client.stream_chat(model_id="openai-main", api_key=KEY, messages=[])
    assert await anext(tokens) == "a"
    await tokens.aclose()  # user pressed stop / client disconnected
    assert stream.closed


@pytest.mark.parametrize("model_id", ["missing", "gemini-off"])
async def test_stream_chat_unknown_or_disabled_model(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict], model_id: str
) -> None:
    _patch_acompletion(monkeypatch, calls, FakeStream([]))
    with pytest.raises(ValueError):
        await _collect(model_id=model_id, api_key=KEY, messages=[])
    assert calls == []


# --- validate_key -------------------------------------------------------------------------


async def test_validate_key_calls_validation_model(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict]
) -> None:
    _patch_acompletion(monkeypatch, calls, SimpleNamespace(choices=[]))
    assert await llm_client.validate_key(provider="gemini", api_key=KEY) is True
    [call] = calls
    assert call["model"] == "gemini/cheap-model"
    assert call["api_key"] == KEY
    assert call["max_tokens"] == 1
    assert "stream" not in call


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (_error(litellm.AuthenticationError), "invalid_key"),
        (_error(litellm.RateLimitError), "rate_limited"),
        (_error(litellm.Timeout), "provider_error"),
    ],
)
async def test_validate_key_maps_errors(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict],
    caplog: pytest.LogCaptureFixture,
    exc: Exception,
    code: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    _patch_acompletion(monkeypatch, calls, exc)
    with pytest.raises(LLMError) as info:
        await llm_client.validate_key(provider="openai", api_key=KEY)
    assert info.value.code == code
    _assert_no_leak(info.value, caplog)


async def test_validate_key_placeholder_model_is_not_checked(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict]
) -> None:
    _patch_acompletion(monkeypatch, calls, SimpleNamespace(choices=[]))
    assert await llm_client.validate_key(provider="anthropic", api_key=KEY) is False
    assert calls == []


async def test_validate_key_rejects_malformed_key_without_call(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict]
) -> None:
    _patch_acompletion(monkeypatch, calls, SimpleNamespace(choices=[]))
    for bad in ("", "sk with space"):
        with pytest.raises(LLMError) as info:
            await llm_client.validate_key(provider="openai", api_key=bad)
        assert info.value.code == "invalid_key"
    assert calls == []
