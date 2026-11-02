import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.core.crypto import InvalidToken, build_fernet, decrypt_secret, encrypt_secret, rotate

SECRET = "sk-test-Tiếng-Việt-1234"


def test_encrypt_decrypt_roundtrip() -> None:
    token = encrypt_secret(SECRET)
    assert isinstance(token, bytes)
    assert SECRET.encode() not in token
    assert decrypt_secret(token) == SECRET


def test_encrypt_is_randomized() -> None:
    assert encrypt_secret(SECRET) != encrypt_secret(SECRET)


def test_decrypt_with_wrong_key_raises() -> None:
    token = encrypt_secret(SECRET)
    other = build_fernet([Fernet.generate_key().decode()])
    with pytest.raises(InvalidToken):
        decrypt_secret(token, fernet=other)


def test_decrypt_tampered_token_raises() -> None:
    token = bytearray(encrypt_secret(SECRET))
    token[-5] ^= 1
    with pytest.raises(InvalidToken):
        decrypt_secret(bytes(token))


def test_rotate_to_new_key() -> None:
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    old_only = build_fernet([old_key])
    token = encrypt_secret(SECRET, fernet=old_only)

    # New key first (encrypts), old key kept for decryption during rotation.
    rotating = build_fernet([SecretStr(new_key), SecretStr(old_key)])
    assert decrypt_secret(token, fernet=rotating) == SECRET
    rotated = rotate(token, fernet=rotating)

    new_only = build_fernet([new_key])
    assert decrypt_secret(rotated, fernet=new_only) == SECRET
    with pytest.raises(InvalidToken):
        decrypt_secret(rotated, fernet=old_only)


def test_build_fernet_requires_a_key() -> None:
    with pytest.raises(ValueError):
        build_fernet([])


def test_build_fernet_names_bad_key_position_only() -> None:
    bad = "not-a-fernet-key-xyz"
    with pytest.raises(ValueError, match="entry #2") as info:
        build_fernet([Fernet.generate_key().decode(), SecretStr(bad)])
    assert bad not in str(info.value)
    assert info.value.__cause__ is None
