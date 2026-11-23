"""Прикладные ошибки. Все превращаются в единый формат: code, message, details, request_id."""

from typing import Any


class ApiError(Exception):
    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details


class NotFoundError(ApiError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(ApiError):
    status_code = 409
    code = "CONFLICT"


class InvalidTransitionError(ConflictError):
    code = "INVALID_STATUS_TRANSITION"


class UnprocessableError(ApiError):
    status_code = 422
    code = "UNPROCESSABLE"


class ServiceUnavailableError(ApiError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
