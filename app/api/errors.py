"""Единый формат ошибок: {code, message, details, request_id}."""

import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic_core import to_jsonable_python
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import ApiError

log = logging.getLogger("civicflow.api")
_RID_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _error(request: Request, status: int, code: str, message: str, details=None) -> JSONResponse:
    rid = _rid(request)
    body = {"code": code, "message": message, "details": to_jsonable_python(details), "request_id": rid}
    headers = {"X-Request-ID": rid} if rid else None
    return JSONResponse(status_code=status, content=body, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if _RID_OK.match(incoming) else uuid.uuid4().hex
        request.state.request_id = rid
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        # Логируем только метаданные — без тел запросов (там могут быть персональные данные).
        log.info(
            "%s %s -> %s (%.0f ms) rid=%s",
            request.method, request.url.path, response.status_code, (time.perf_counter() - started) * 1000, rid,
        )
        return response

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return _error(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        details = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()
        ]
        return _error(request, 422, "VALIDATION_ERROR", "Некорректные входные данные", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return _error(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError):
        log.warning("integrity error rid=%s: %s", _rid(request), exc.orig)
        return _error(request, 409, "INTEGRITY_ERROR", "Нарушено ограничение целостности данных")

    @app.exception_handler(SQLAlchemyError)
    async def _db(request: Request, exc: SQLAlchemyError):
        log.exception("db error rid=%s", _rid(request))
        if isinstance(exc, OperationalError):
            return _error(request, 503, "DB_UNAVAILABLE", "База данных недоступна, повторите позже")
        return _error(request, 500, "DB_ERROR", "Ошибка базы данных")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error rid=%s", _rid(request))
        return _error(request, 500, "INTERNAL_ERROR", "Внутренняя ошибка сервера")
