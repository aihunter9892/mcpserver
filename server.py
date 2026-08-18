"""
LLM Toolkit MCP Server
======================
A minimal-but-real MCP server that exposes an LLM API as MCP tools.

Ships with two interchangeable backends, picked at startup by $LLM_PROVIDER:
  * groq       -> fast, free tier, OpenAI-compatible  (default)
  * anthropic  -> Claude

Runs in two transports:
  * stdio  -> for local clients (Claude Desktop, Claude Code, Cursor)
  * http   -> for remote deployment (Render, Railway, Fly.io, any container host)

Usage:
    python server.py                          # stdio, Groq
    python server.py --http                   # streamable HTTP on 0.0.0.0:$PORT
    LLM_PROVIDER=anthropic python server.py   # same tools, Claude behind them

Production concerns (auth, rate limiting, size caps, logging) live in guards.py
so this file stays readable.
"""

import functools
import logging
import os
import sys

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from guards import (
    MAX_INPUT_CHARS,
    InputTooLarge,
    RateLimiter,
    SecurityMiddleware,
    check_size,
    logging_middleware,
    setup_logging,
)

# Load .env sitting next to this file, so the server works no matter which
# directory the MCP client launches it from. Real env vars always win, which is
# what you want on a deployed host (Render/Fly inject them directly).
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

setup_logging()
logger = logging.getLogger("llm-toolkit")

# ---------------------------------------------------------------------------
# 1. Config - everything provider-specific is declared in this one table
#
#    `fallbacks` exists because providers retire model IDs without warning.
#    That is not hypothetical: Groq removed llama-3.3-70b-versatile while this
#    server was being built, turning every tool call into a silent 404.
# ---------------------------------------------------------------------------

PROVIDERS = {
    "groq": {
        "key_var": "GROQ_API_KEY",
        "model_var": "GROQ_MODEL",
        "default_model": "openai/gpt-oss-120b",
        "fallbacks": ["openai/gpt-oss-20b", "qwen/qwen3.6-27b"],
    },
    "anthropic": {
        "key_var": "ANTHROPIC_API_KEY",
        "model_var": "ANTHROPIC_MODEL",
        "default_model": "claude-sonnet-5",
        "fallbacks": ["claude-haiku-4-5-20251001"],
    },
}

PROVIDER = os.environ.get("LLM_PROVIDER", "groq").strip().lower()

if PROVIDER not in PROVIDERS:
    print(
        f"ERROR: LLM_PROVIDER={PROVIDER} is not supported. "
        f"Choose one of: {', '.join(PROVIDERS)}.",
        file=sys.stderr,
    )
    sys.exit(1)

conf = PROVIDERS[PROVIDER]
MODEL = os.environ.get(conf["model_var"]) or conf["default_model"]
API_KEY = os.environ.get(conf["key_var"])

# Models to try, in order, if the primary one is gone.
MODEL_CHAIN = [MODEL] + [m for m in conf["fallbacks"] if m != MODEL]

TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "45"))
MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "2"))

if not API_KEY:
    print(
        f"ERROR: {conf['key_var']} is not set, but LLM_PROVIDER={PROVIDER}.",
        file=sys.stderr,
    )
    sys.exit(1)

# Import only the SDK we actually need, so students can run this with just one
# of the two packages installed. Both SDKs retry and time out for us.
if PROVIDER == "groq":
    from groq import Groq

    client = Groq(api_key=API_KEY, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)
else:
    import anthropic

    client = anthropic.Anthropic(
        api_key=API_KEY, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES
    )

mcp = MCPServer("llm-toolkit", version="2.0.0", middleware=[logging_middleware])


# ---------------------------------------------------------------------------
# 2. Shared helper - the ONLY place that knows which vendor is answering
#
#    This is the seam. The four tools below call call_llm() and never learn
#    whether Groq or Claude replied. Swapping vendors, or adding a third one,
#    touches this function and the PROVIDERS table above. Nothing else.
# ---------------------------------------------------------------------------

def _one_call(model: str, prompt: str, system: str, max_tokens: int) -> str:
    """Single attempt against one specific model."""
    if PROVIDER == "groq":
        # OpenAI-compatible: the system prompt is just another message.
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens
        )
        return response.choices[0].message.content or ""

    # Anthropic: the system prompt is a top-level argument, not a message.
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    return "".join(b.text for b in response.content if b.type == "text")


def _is_missing_model(exc: Exception) -> bool:
    """True when the provider says the model ID does not exist."""
    text = str(exc).lower()
    return "404" in text or "does not exist" in text or "not_found" in text


def call_llm(prompt: str, system: str = "", max_tokens: int = 1024) -> str:
    """Send a single-turn message to the model and return the text response.

    Walks MODEL_CHAIN so a retired model ID degrades to the next one instead of
    taking every tool down.
    """
    last_error = None

    for model in MODEL_CHAIN:
        try:
            return _one_call(model, prompt, system, max_tokens)
        except Exception as exc:
            last_error = exc
            if _is_missing_model(exc) and model != MODEL_CHAIN[-1]:
                logger.warning("model %s unavailable, falling back", model)
                continue
            break

    # Return the error as text so the MCP client can show it to the user
    # instead of the whole tool call blowing up.
    logger.error("llm call failed: %s", last_error)
    return f"{PROVIDER} API error: {last_error}"


def _guarded(fn):
    """Turn an oversized-input rejection into a readable tool result.

    functools.wraps matters here: MCP builds each tool's JSON schema from the
    function signature, and a bare *args/**kwargs wrapper would erase it. wraps
    sets __wrapped__, which inspect.signature follows back to the real one.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except InputTooLarge as exc:
            return f"Input rejected: {exc}"

    return wrapper


# ---------------------------------------------------------------------------
# 3. Tools - each @mcp.tool() becomes a callable tool in the MCP client
#    The docstring IS the tool description the model reads. Write it well.
# ---------------------------------------------------------------------------

@mcp.tool()
@_guarded
def ask_llm(question: str, style: str = "concise") -> str:
    """Ask the model a question and get an answer.

    Args:
        question: The question to ask.
        style: Answer style - "concise", "detailed", or "eli5".
    """
    check_size(question, "question")
    styles = {
        "concise": "Answer in 3 sentences or fewer. No preamble.",
        "detailed": "Answer thoroughly with examples and edge cases.",
        "eli5": "Explain like the reader is five years old. Use simple analogies.",
    }
    system = styles.get(style, styles["concise"])
    return call_llm(question, system=system)


@mcp.tool()
@_guarded
def summarize(text: str, bullets: int = 5) -> str:
    """Summarize a block of text into a fixed number of bullet points.

    Args:
        text: The text to summarize.
        bullets: How many bullet points to produce (1-10).
    """
    check_size(text, "text")
    bullets = max(1, min(bullets, 10))
    system = (
        f"Summarize the user's text into exactly {bullets} bullet points. "
        "Each bullet is one line. Output only the bullets, no heading."
    )
    return call_llm(text, system=system)


@mcp.tool()
@_guarded
def translate(text: str, target_language: str, tone: str = "neutral") -> str:
    """Translate text into a target language, preserving formatting.

    Args:
        text: The text to translate.
        target_language: e.g. "Hindi", "Spanish", "Japanese".
        tone: "neutral", "formal", or "casual".
    """
    check_size(text, "text")
    system = (
        f"Translate the user's text into {target_language}. "
        f"Use a {tone} tone. Preserve markdown, line breaks, and code blocks. "
        "Output only the translation."
    )
    return call_llm(text, system=system)


@mcp.tool()
@_guarded
def extract_json(text: str, fields: str) -> str:
    """Pull structured JSON out of unstructured text.

    Args:
        text: The unstructured source text.
        fields: Comma-separated field names, e.g. "name,email,company".
    """
    check_size(text, "text")
    system = (
        f"Extract these fields from the user's text: {fields}. "
        "Respond with a single valid JSON object and nothing else. "
        "Use null for any field you cannot find."
    )
    return call_llm(text, system=system)


# ---------------------------------------------------------------------------
# 4. A resource - read-only data the client can pull in as context
#
#    Handy in class: read this before and after flipping LLM_PROVIDER to prove
#    the backend really changed.
# ---------------------------------------------------------------------------

@mcp.resource("config://server-info")
def server_info() -> str:
    """Show which provider and model this server is running."""
    return (
        f"llm-toolkit MCP server\n"
        f"provider: {PROVIDER}\n"
        f"model: {MODEL}\n"
        f"fallbacks: {', '.join(MODEL_CHAIN[1:]) or 'none'}\n"
        f"max input chars: {MAX_INPUT_CHARS}\n"
        f"tools: 4"
    )


# ---------------------------------------------------------------------------
# 5. A prompt - a reusable prompt template the user can invoke
# ---------------------------------------------------------------------------

@mcp.prompt()
def code_review(code: str) -> str:
    """Generate a code review prompt for the given code."""
    return (
        "Review this code. Call out correctness bugs first, then simplifications. "
        f"Be specific and cite line content.\n\n```\n{code}\n```"
    )


# ---------------------------------------------------------------------------
# 6. Health endpoint
#
#    /mcp cannot be health-checked: it is an SSE stream that never closes, so
#    a probe against it hangs and the platform restart-loops the service.
#    This is a plain JSON 200 instead.
# ---------------------------------------------------------------------------

@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    from starlette.responses import JSONResponse

    return JSONResponse({
        "status": "ok",
        "provider": PROVIDER,
        "model": MODEL,
        "tools": 4,
        "version": "2.0.0",
    })


# ---------------------------------------------------------------------------
# 7. Entrypoint
# ---------------------------------------------------------------------------

def build_http_app():
    """Streamable-HTTP ASGI app with the security guards wrapped around it."""
    app = mcp.streamable_http_app(stateless_http=True)
    return SecurityMiddleware(app, RateLimiter(
        int(os.environ.get("RATE_LIMIT_PER_MIN", "30"))
    ))


if __name__ == "__main__":
    logger.info("starting provider=%s model=%s chain=%s", PROVIDER, MODEL, MODEL_CHAIN)

    if "--http" in sys.argv:
        import uvicorn

        uvicorn.run(
            build_http_app(),
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 8000)),
            log_level=os.environ.get("LOG_LEVEL", "info").lower(),
        )
    else:
        # Local mode. The client launches this process and talks over stdin/stdout.
        mcp.run(transport="stdio")
