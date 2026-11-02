"""Generate per-machine secrets for `.env`.

Usage (from backend/):
    uv run python scripts/gen_keys.py >> ../.env

To rotate the encryption key, put the new key first and keep the old ones after it:
    APP_ENCRYPTION_KEYS=<new>,<old>
"""

import secrets

from cryptography.fernet import Fernet


def generate() -> dict[str, str]:
    return {
        "APP_ENCRYPTION_KEYS": Fernet.generate_key().decode(),
        "JWT_SECRET": secrets.token_urlsafe(64),
    }


def main() -> None:
    for name, value in generate().items():
        print(f"{name}={value}")


if __name__ == "__main__":
    main()
