"""Try the LLM client with a real API key (T17). Needs no database.

Usage (from backend/):
    uv run python scripts/smoke_llm.py --provider openai
    uv run python scripts/smoke_llm.py --provider gemini --model-id gemini-main --validate-only

The key is read from LLM_API_KEY or typed at a hidden prompt; never pass it as an argument
(it would end up in the shell history and the process list). The key is never printed.
Model names come from config/models.yaml: fill in the `<...>` placeholders first.
"""

# ruff: noqa: E402  (imports must follow the sys.path setup below)
import sys
from pathlib import Path

# Run as `python scripts/smoke_llm.py`: make `app` importable from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import asyncio
import os
import time
from getpass import getpass

from app.db.models import PROVIDERS
from app.llm import client
from app.llm.errors import LLMError
from app.llm.registry import get_registry

QUESTION = "Trả lời đúng một câu ngắn bằng tiếng Việt: Thủ đô của Việt Nam là gì?"


async def run(provider: str, model_id: str, api_key: str, validate_only: bool) -> int:
    started = time.perf_counter()
    try:
        checked = await client.validate_key(provider=provider, api_key=api_key)
    except LLMError as exc:
        print(f"validate_key: FAILED code={exc.code} ({exc.user_message})")
        return 1
    status = "OK" if checked else "NOT CHECKED (validation_model is a placeholder)"
    print(f"validate_key: {status} in {time.perf_counter() - started:.1f}s")
    if validate_only:
        return 0

    print(f"stream_chat model={model_id}:")
    started = time.perf_counter()
    first_token_at: float | None = None
    tokens = 0
    try:
        async for token in client.stream_chat(
            model_id=model_id, api_key=api_key, messages=[{"role": "user", "content": QUESTION}]
        ):
            first_token_at = first_token_at or time.perf_counter()
            tokens += 1
            print(token, end="", flush=True)
    except LLMError as exc:
        print(f"\nstream_chat: FAILED code={exc.code} ({exc.user_message})")
        return 1
    total = time.perf_counter() - started
    ttft = f"{first_token_at - started:.1f}s" if first_token_at else "-"
    print(f"\nstream_chat: OK, {tokens} chunks, first token {ttft}, total {total:.1f}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", required=True, choices=PROVIDERS)
    parser.add_argument("--model-id", help="model id in models.yaml (default: first enabled)")
    parser.add_argument("--validate-only", action="store_true", help="only call validate_key")
    args = parser.parse_args()

    registry = get_registry()
    if args.model_id:
        spec = registry.get_enabled(args.model_id)
        if spec is None or spec.provider != args.provider:
            parser.error(f"no enabled {args.provider} model with id {args.model_id!r}")
    else:
        spec = next((m for m in registry.enabled_models() if m.provider == args.provider), None)
        if spec is None and not args.validate_only:
            parser.error(f"no enabled {args.provider} model in models.yaml")

    api_key = os.environ.get("LLM_API_KEY") or getpass(f"{args.provider} API key: ")
    api_key = api_key.strip()
    model_id = spec.id if spec else ""
    return asyncio.run(run(args.provider, model_id, api_key, args.validate_only))


if __name__ == "__main__":
    sys.exit(main())
