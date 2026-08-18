"""
Production guards for the MCP server.

Kept in its own module so server.py stays readable as teaching material.
Nothing here is MCP-specific except LoggingMiddleware; the rest is ordinary
web-service hygiene that any public endpoint needs.

What this covers:
  * bearer-token auth       (optional - off by default so the server stays open)
  * per-IP rate limiting    (always on - protects your API key from abuse)
  * request body size cap   (rejects oversized payloads before parsing)
  * a real /health endpoint (plain JSON, unlike /mcp which is an SSE stream)
  * structured request logging for both stdio and HTTP transports
"""

import json
import logging
import os
import time
from collections import defaultdict, deque

logger = logging.getLogger("llm-toolkit")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _int_env(name: str, default: int) -> int:
    """Read an int from the environment, falling back if unset or malformed."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer, using %d", name, raw, default)
        return default


# If unset, the server is open to anyone who knows the URL. That is a
# deliberate default for workshops. Set it before pointing a paid key at this.
AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").strip()

RATE_LIMIT_PER_MIN = _int_env("RATE_LIMIT_PER_MIN", 30)
MAX_BODY_BYTES = _int_env("MAX_BODY_BYTES", 1_000_000)  # 1 MB
MAX_INPUT_CHARS = _int_env("MAX_INPUT_CHARS", 20_000)


def setup_logging() -> None:
    """Log to stderr only.

    stdout carries the JSON-RPC stream in stdio mode, so anything printed there
    corrupts the protocol. This is the single most common way to break an MCP
    server: a stray print() during debugging.
    """
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=__import__("sys").stderr,
    )


# ---------------------------------------------------------------------------
# Input validation - used by the tools themselves
# ---------------------------------------------------------------------------

class InputTooLarge(ValueError):
    """Raised when a tool argument exceeds MAX_INPUT_CHARS."""


def check_size(value: str, field: str) -> str:
    """Reject oversized tool arguments before they reach the LLM.

    Without this, one pasted novel burns your whole token budget in a single
    call. Fails fast and cheaply instead.
    """
    if len(value) > MAX_INPUT_CHARS:
        raise InputTooLarge(
            f"{field} is {len(value)} characters, limit is {MAX_INPUT_CHARS}. "
            "Split the input and call the tool more than once."
        )
    return value


# ---------------------------------------------------------------------------
# Rate limiting
#
# In-memory sliding window, per client IP. Per-process, so N instances allow
# N times the limit - fine for one free-tier instance, and the right place to
# swap in Redis if you ever scale out.
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, client: str) -> bool:
        if self.per_minute <= 0:  # 0 or negative disables limiting
            return True

        now = time.monotonic()
        window = self._hits[client]

        while window and now - window[0] > 60.0:
            window.popleft()

        if len(window) >= self.per_minute:
            return False

        window.append(now)

        # Stop unbounded growth from one-off IPs that never come back.
        if len(self._hits) > 10_000:
            for ip in [k for k, v in self._hits.items() if not v]:
                del self._hits[ip]

        return True


# ---------------------------------------------------------------------------
# ASGI middleware
#
# Written as raw ASGI rather than Starlette's BaseHTTPMiddleware on purpose:
# BaseHTTPMiddleware buffers responses, which breaks the Server-Sent Events
# stream that MCP's streamable HTTP transport depends on.
# ---------------------------------------------------------------------------

class SecurityMiddleware:
    """Auth, rate limiting, and body-size enforcement for the HTTP transport."""

    def __init__(self, app, rate_limiter: RateLimiter):
        self.app = app
        self.limiter = rate_limiter

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # /health must stay open, or the platform cannot check the service.
        if path == "/health":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}

        if AUTH_TOKEN:
            provided = headers.get("authorization", "")
            expected = f"Bearer {AUTH_TOKEN}"
            # Constant-time compare so response timing cannot leak the token.
            import hmac
            if not hmac.compare_digest(provided, expected):
                logger.warning("rejected unauthenticated request to %s", path)
                await _json_response(send, 401, {
                    "error": "unauthorized",
                    "detail": "Send: Authorization: Bearer <token>",
                })
                return

        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            await _json_response(send, 413, {
                "error": "payload_too_large",
                "limit_bytes": MAX_BODY_BYTES,
            })
            return

        client = scope.get("client")
        client_ip = headers.get("x-forwarded-for", "").split(",")[0].strip() or (
            client[0] if client else "unknown"
        )

        if not self.limiter.allow(client_ip):
            logger.warning("rate limited %s", client_ip)
            await _json_response(send, 429, {
                "error": "rate_limited",
                "limit_per_minute": self.limiter.per_minute,
                "detail": "Slow down and retry shortly.",
            }, extra_headers=[(b"retry-after", b"60")])
            return

        await self.app(scope, receive, send)


async def _json_response(send, status: int, payload: dict, extra_headers=None):
    body = json.dumps(payload).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# MCP protocol middleware - runs for BOTH stdio and HTTP
# ---------------------------------------------------------------------------

async def logging_middleware(ctx, call_next):
    """Log every MCP method with its duration and outcome."""
    started = time.monotonic()
    try:
        result = await call_next(ctx)
    except Exception as exc:
        logger.warning(
            "mcp method=%s failed in %.0fms: %s",
            ctx.method, (time.monotonic() - started) * 1000, exc,
        )
        raise
    logger.info(
        "mcp method=%s ok in %.0fms",
        ctx.method, (time.monotonic() - started) * 1000,
    )
    return result
