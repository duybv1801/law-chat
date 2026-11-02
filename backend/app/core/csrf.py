"""CSRF guard: state-changing requests must carry `X-Requested-With: fetch`.

Browsers cannot set this header on cross-site form posts, and a cross-site `fetch` with it
triggers a CORS preflight that we do not allow. Together with `SameSite=Lax` cookies this is
enough for now (docs/06-api.md, Auth).
"""

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import error_response

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = b"x-requested-with"
CSRF_VALUE = b"fetch"


class CsrfHeaderMiddleware:
    """Pure ASGI middleware (no response buffering, so SSE streams pass through untouched)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            values = [v for k, v in scope["headers"] if k == CSRF_HEADER]
            if values != [CSRF_VALUE]:
                response = error_response(
                    403, "csrf_failed", "Yêu cầu không hợp lệ (thiếu header X-Requested-With)."
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
