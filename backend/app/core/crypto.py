"""Fernet/MultiFernet encryption for user API keys (docs/05-rag-chat.md, "Mã hoá key").

`APP_ENCRYPTION_KEYS` is a comma-separated list: the first key encrypts, every key can
decrypt. To rotate the master key, prepend a new key, `rotate()` stored tokens, then drop
the old key.
"""

from collections.abc import Sequence
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import SecretStr

__all__ = ["InvalidToken", "build_fernet", "decrypt_secret", "encrypt_secret", "rotate"]


def build_fernet(keys: Sequence[SecretStr | str]) -> MultiFernet:
    """Raise `ValueError` naming only the position (never the value) of an invalid key."""
    if not keys:
        raise ValueError("at least one encryption key is required")
    fernets = []
    for index, key in enumerate(keys):
        try:
            fernets.append(Fernet(key.get_secret_value() if isinstance(key, SecretStr) else key))
        except ValueError:
            raise ValueError(
                f"APP_ENCRYPTION_KEYS entry #{index + 1} is not a valid Fernet key"
            ) from None
    return MultiFernet(fernets)


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    # Imported here: config validates keys with `build_fernet`, so it imports this module.
    from app.core.config import settings

    return build_fernet(settings.app_encryption_keys)


def encrypt_secret(plain: str, *, fernet: MultiFernet | None = None) -> bytes:
    return (fernet or _fernet()).encrypt(plain.encode("utf-8"))


def decrypt_secret(token: bytes, *, fernet: MultiFernet | None = None) -> str:
    """Raise `InvalidToken` if no configured key can decrypt `token` (or it was tampered)."""
    return (fernet or _fernet()).decrypt(token).decode("utf-8")


def rotate(token: bytes, *, fernet: MultiFernet | None = None) -> bytes:
    """Re-encrypt `token` with the current primary (first) key."""
    return (fernet or _fernet()).rotate(token)
