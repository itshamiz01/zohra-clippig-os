from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from clipos.core.logging import correlation_id_var

logger = logging.getLogger(__name__)


class ClipOSError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class NotFoundError(ClipOSError):
    def __init__(self, resource: str = "resource") -> None:
        super().__init__("not_found", f"{resource} not found", status_code=404)


class AuthorizationError(ClipOSError):
    def __init__(self, message: str = "insufficient permission") -> None:
        super().__init__("forbidden", message, status_code=403)


class AuthenticationError(ClipOSError):
    def __init__(self, message: str = "authentication required") -> None:
        super().__init__("unauthorized", message, status_code=401)


class ConflictError(ClipOSError):
    def __init__(self, message: str) -> None:
        super().__init__("conflict", message, status_code=409)


def _error_payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "correlation_id": correlation_id_var.get(),
        }
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ClipOSError)
    async def handle_domain_error(_: Request, exc: ClipOSError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.code, exc.message, exc.details),
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload("http_error", message),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        safe_errors = [
            {
                "location": [str(item) for item in error.get("loc", [])],
                "message": error.get("msg", "invalid value"),
                "type": error.get("type", "validation_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_payload("validation_error", "request validation failed", safe_errors),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled application error",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"event_data": {"exception_type": type(exc).__name__}},
        )
        return JSONResponse(
            status_code=500,
            content=_error_payload("internal_error", "an internal error occurred"),
        )
