from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque
from typing import Protocol
from uuid import uuid4

from fastapi import Request
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from clipos.core.logging import correlation_id_var

_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get("x-correlation-id", "")
        correlation_id = supplied if _CORRELATION_PATTERN.fullmatch(supplied) else uuid4().hex
        token = correlation_id_var.set(correlation_id)
        request.state.correlation_id = correlation_id
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            correlation_id_var.reset(token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        csp = (
            "default-src 'self' https://cdn.jsdelivr.net; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:"
            if request.url.path.startswith("/docs")
            else "default-src 'none'; frame-ancestors 'none'"
        )
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("Cache-Control", "no-store")
        return response


class RequestSizeMiddleware:
    """Enforce limits for declared and streaming/chunked request bodies."""

    def __init__(self, app: ASGIApp, max_bytes: int, max_upload_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.max_upload_bytes = max_upload_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        request_limit = self.max_upload_bytes if path.endswith("/assets/upload") else self.max_bytes
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                declared_size = int(declared)
                too_large = declared_size < 0 or declared_size > request_limit
            except ValueError:
                too_large = True
            if too_large:
                await self._reject(scope, receive, send)
                return

        received = 0
        body_too_large = False

        async def limited_receive() -> Message:
            nonlocal received, body_too_large
            if body_too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > request_limit:
                    body_too_large = True
                    return {"type": "http.disconnect"}
            return message

        async def tracked_send(message: Message) -> None:
            if not body_too_large:
                await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except ClientDisconnect:
            if not body_too_large:
                raise
        if body_too_large:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        state = scope.get("state", {})
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "request_too_large",
                    "message": "request exceeds configured size limit",
                    "details": {},
                    "correlation_id": state.get("correlation_id", "unbound"),
                }
            },
        )
        await response(scope, receive, send)


class RateLimiter(Protocol):
    async def allow(self, key: str) -> tuple[bool, int]: ...

    async def close(self) -> None: ...


class MemoryRateLimiter:
    """Process-local development limiter.

    Production must use a shared edge/Redis limiter; configuration validation prevents the
    memory queue in protected environments, and the architecture report records this limit.
    """

    def __init__(self, requests_per_minute: int) -> None:
        self.limit = requests_per_minute
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - 60.0
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            remaining = max(0, self.limit - len(events))
            if len(events) >= self.limit:
                return False, 0
            events.append(now)
            return True, max(0, remaining - 1)

    async def close(self) -> None:
        return None


class RedisRateLimiter:
    """Shared fixed-window limiter for multi-process protected environments."""

    def __init__(self, url: str, requests_per_minute: int) -> None:
        self.limit = requests_per_minute
        self._redis = Redis.from_url(url, decode_responses=True)

    async def allow(self, key: str) -> tuple[bool, int]:
        window = int(time.time() // 60)
        redis_key = f"clipos:rate:v1:{window}:{key}"
        async with self._redis.pipeline(transaction=True) as pipeline:
            pipeline.incr(redis_key)
            pipeline.expire(redis_key, 70)
            count, _ = await pipeline.execute()
        count = int(count)
        return count <= self.limit, max(0, self.limit - count)

    async def close(self) -> None:
        await self._redis.aclose()


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, limiter: RateLimiter) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in {"/healthz", "/readyz"}:
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        try:
            allowed, remaining = await self.limiter.allow(client)
        except Exception:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "rate_limiter_unavailable",
                        "message": "request safety dependency is unavailable",
                        "details": {},
                        "correlation_id": getattr(request.state, "correlation_id", "unbound"),
                    }
                },
            )
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "60", "X-RateLimit-Remaining": "0"},
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "request rate limit exceeded",
                        "details": {},
                        "correlation_id": getattr(request.state, "correlation_id", "unbound"),
                    }
                },
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
