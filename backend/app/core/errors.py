"""Unified API error format: `{"error": {"code": ..., "message": ...}}` (docs/06-api.md)."""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Error with a stable machine `code` and a Vietnamese `message` for the user."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def error_response(
    status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code, headers=headers
    )


_HTTP_CODES = {
    status.HTTP_401_UNAUTHORIZED: ("not_authenticated", "Bạn cần đăng nhập."),
    status.HTTP_403_FORBIDDEN: ("forbidden", "Bạn không có quyền thực hiện thao tác này."),
    status.HTTP_404_NOT_FOUND: ("not_found", "Không tìm thấy."),
    status.HTTP_405_METHOD_NOT_ALLOWED: ("method_not_allowed", "Phương thức không được hỗ trợ."),
}


async def _app_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return error_response(exc.status_code, exc.code, exc.message, exc.headers)


async def _http_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code, message = _HTTP_CODES.get(exc.status_code, ("http_error", "Yêu cầu không hợp lệ."))
    return error_response(exc.status_code, code, message, exc.headers)


async def _validation_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Only field locations, never the input values (they may contain passwords or API keys).
    fields = sorted({".".join(str(p) for p in err["loc"][1:]) for err in exc.errors()} - {""})
    message = "Dữ liệu không hợp lệ"
    if fields:
        message += f": {', '.join(fields)}"
    return error_response(status.HTTP_422_UNPROCESSABLE_CONTENT, "validation_error", message)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
