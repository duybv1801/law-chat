"""Auth request/response schemas."""

import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Pragmatic check (no `email-validator` dependency): one "@", a dot in the domain, no spaces.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Mirrored in frontend/src/api/auth.ts (kept in sync by tests/test_auth.py).
EMAIL_MAX = 254
PASSWORD_MIN = 8
PASSWORD_MAX = 128  # bounds argon2 work per request


class Credentials(BaseModel):
    email: str = Field(max_length=EMAIL_MAX)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not _EMAIL_RE.match(value):
            raise ValueError("invalid email")
        return value


class RegisterRequest(Credentials):
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class LoginRequest(Credentials):
    pass


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
