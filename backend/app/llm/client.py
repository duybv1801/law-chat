"""LiteLLM streaming client and key validation (docs/05-rag-chat.md, "LLM client").

The user's key is passed to every call as `api_key`, never put in `os.environ` (many users
share one process). LiteLLM is imported lazily (it takes ~2s) and configured once so it
never logs messages, request bodies or keys (rule 1).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from functools import cache
from types import ModuleType

from app.llm.errors import LLMError, LLMErrorCode
from app.llm.registry import PLACEHOLDER_MARK, get_registry

logger = logging.getLogger(__name__)

TEMPERATURE = 0.1
# Per request (connect / between stream chunks), not for the whole answer.
CHAT_TIMEOUT_S = 60.0
VALIDATE_TIMEOUT_S = 20.0
VALIDATE_MESSAGES = [{"role": "user", "content": "ping"}]

_LITELLM_LOGGERS = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy")


@cache
def _litellm() -> ModuleType:
    """Import LiteLLM and turn off everything that could log prompts, bodies or keys."""
    import litellm

    litellm.turn_off_message_logging = True
    litellm.redact_messages_in_exceptions = True
    litellm.log_raw_request_response = False
    litellm.suppress_debug_info = True  # no "Give Feedback / Get Help" dumps on stdout
    for name in _LITELLM_LOGGERS:
        # Even if LITELLM_LOG=DEBUG is set in the shell: debug lines include request details.
        logging.getLogger(name).setLevel(logging.WARNING)
    return litellm


def map_exception(exc: Exception) -> LLMError:
    """Map a LiteLLM / provider exception to `LLMError` (docs/05-rag-chat.md).

    Order matters: `ContextWindowExceededError` is a `BadRequestError`.
    """
    litellm = _litellm()
    code: LLMErrorCode
    if isinstance(exc, LLMError):
        return exc
    if isinstance(exc, litellm.AuthenticationError | litellm.PermissionDeniedError):
        code = "invalid_key"
    elif isinstance(exc, litellm.RateLimitError):
        code = "rate_limited"
    elif isinstance(exc, litellm.ContextWindowExceededError):
        code = "context_too_long"
    else:
        code = "provider_error"
    return LLMError(code)


def _log_failure(action: str, provider: str, exc: Exception, code: LLMErrorCode) -> None:
    # Type and status only: the message / args may echo the request (prompt, sometimes key).
    logger.warning(
        "llm %s failed: provider=%s error=%s status=%s code=%s",
        action,
        provider,
        type(exc).__name__,
        getattr(exc, "status_code", None),
        code,
    )


async def validate_key(*, provider: str, api_key: str) -> bool:
    """Raise `LLMError` (`invalid_key`, ...) if `api_key` is not usable for `provider`.

    Calls the provider's `validation_model` with `max_tokens=1`. Return True if the provider
    accepted the key (sets `validated_at`), False if it could not be checked because
    `validation_model` is still a `<...>` placeholder in models.yaml.
    """
    if not api_key or any(ch.isspace() for ch in api_key):
        raise LLMError("invalid_key")
    model = get_registry().validation_model[provider]
    if PLACEHOLDER_MARK in model:
        logger.warning("api key not checked: validation_model.%s is a placeholder", provider)
        return False
    litellm = _litellm()
    try:
        await litellm.acompletion(
            model=model,
            api_key=api_key,
            messages=VALIDATE_MESSAGES,
            max_tokens=1,
            timeout=VALIDATE_TIMEOUT_S,
            drop_params=True,
        )
    except Exception as exc:
        error = map_exception(exc)
        _log_failure("key check", provider, exc, error.code)
        raise error from None
    return True


async def stream_chat(*, model_id: str, api_key: str, messages: list[dict]) -> AsyncIterator[str]:
    """Stream answer tokens from the enabled model `model_id` using the user's `api_key`.

    Raise `ValueError` for an unknown / disabled model (the chat service checks it first) and
    `LLMError` for any provider failure, also after some tokens were already yielded.
    """
    spec = get_registry().get_enabled(model_id)
    if spec is None:
        raise ValueError(f"unknown or disabled model: {model_id}")
    litellm = _litellm()
    stream = None
    try:
        stream = await litellm.acompletion(
            model=spec.litellm_model,
            api_key=api_key,
            messages=messages,
            stream=True,
            temperature=TEMPERATURE,
            timeout=CHAT_TIMEOUT_S,
            # Some models only accept their default temperature: drop it instead of failing.
            drop_params=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            text = chunk.choices[0].delta.content
            if text:
                yield text
    except Exception as exc:
        error = map_exception(exc)
        _log_failure("chat", spec.provider, exc, error.code)
        raise error from None
    finally:
        # Stop button / client gone: close the provider connection instead of draining it.
        if stream is not None and hasattr(stream, "aclose"):
            with suppress(Exception):
                await stream.aclose()
