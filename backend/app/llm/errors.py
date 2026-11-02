"""LLM error types. Mapping of LiteLLM / provider exceptions is completed in T17."""

from typing import Literal

LLMErrorCode = Literal["invalid_key", "rate_limited", "context_too_long", "provider_error"]

USER_MESSAGES: dict[LLMErrorCode, str] = {
    "invalid_key": "API key không hợp lệ hoặc đã bị thu hồi. Vào Cài đặt để cập nhật.",
    "rate_limited": "Nhà cung cấp đang giới hạn tần suất. Vui lòng thử lại sau ít phút.",
    "context_too_long": "Nội dung quá dài so với giới hạn của model. Hãy rút gọn câu hỏi.",
    "provider_error": "Nhà cung cấp mô hình đang gặp lỗi. Vui lòng thử lại sau.",
}


class LLMError(Exception):
    """Provider error with a stable `code` and a Vietnamese `user_message`.

    Never put the API key, the prompt or the raw provider response into the message.
    """

    def __init__(self, code: LLMErrorCode, user_message: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.user_message = user_message or USER_MESSAGES[code]
