"""LLM error types. LiteLLM / provider exceptions are mapped in `app.llm.client.map_exception`."""

from typing import Literal

LLMErrorCode = Literal[
    "invalid_key",
    "access_denied",
    "quota_exceeded",
    "rate_limited",
    "context_too_long",
    "request_rejected",
    "provider_error",
]

USER_MESSAGES: dict[LLMErrorCode, str] = {
    "invalid_key": "API key không hợp lệ hoặc đã bị thu hồi. Vào Cài đặt để cập nhật.",
    "access_denied": (
        "API key hợp lệ nhưng không có quyền dùng model này (hoặc khu vực bị chặn). "
        "Kiểm tra quyền truy cập trong tài khoản nhà cung cấp."
    ),
    "quota_exceeded": (
        "Tài khoản nhà cung cấp đã hết hạn mức hoặc hết số dư. "
        "Nạp thêm tiền hoặc kiểm tra gói dịch vụ rồi thử lại."
    ),
    "rate_limited": "Nhà cung cấp đang giới hạn tần suất. Vui lòng thử lại sau ít phút.",
    "context_too_long": "Nội dung quá dài so với giới hạn của model. Hãy rút gọn câu hỏi.",
    "request_rejected": (
        "Nhà cung cấp từ chối yêu cầu (model không tồn tại, tham số hoặc nội dung không được "
        "chấp nhận). Thử model khác hoặc báo quản trị viên."
    ),
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
