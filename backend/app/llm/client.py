"""LiteLLM streaming client and key validation (docs/05-rag-chat.md, "LLM client").

T05 only defines the interface: `validate_key` is a placeholder until T17 calls the
provider's `validation_model` (from `config/models.yaml`, T06) with `max_tokens=1`.
"""

from collections.abc import AsyncIterator

from app.llm.errors import LLMError


async def validate_key(*, provider: str, api_key: str) -> bool:
    """Raise `LLMError` (`invalid_key`, ...) if `api_key` is not usable for `provider`.

    Return True only if the provider itself accepted the key (sets `validated_at`).
    Placeholder (T17 replaces it): only rejects keys that cannot possibly be valid and
    returns False, since nothing was checked with the provider.
    """
    if not api_key or any(ch.isspace() for ch in api_key):
        raise LLMError("invalid_key")
    return False


async def stream_chat(*, model_id: str, api_key: str, messages: list[dict]) -> AsyncIterator[str]:
    """Stream answer tokens from `model_id` using the user's `api_key` (implemented in T17)."""
    raise NotImplementedError("stream_chat is implemented in T17")
    yield ""  # pragma: no cover - makes this an async generator
