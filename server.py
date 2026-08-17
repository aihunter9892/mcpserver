"""
LLM Toolkit MCP Server
======================
A minimal-but-real MCP server that exposes an LLM API as MCP tools.
Backed by Groq (fast, OpenAI-compatible, generous free tier).

Runs in two transports:
  * stdio  -> for local clients (Claude Desktop, Claude Code, Cursor)
  * http   -> for remote deployment (Render, Railway, Fly.io, any container host)

Usage:
    python server.py              # stdio (default)
    python server.py --http       # streamable HTTP on 0.0.0.0:$PORT
"""

import os
import sys

from dotenv import load_dotenv
from groq import Groq, GroqError
from mcp.server.mcpserver import MCPServer

# Load .env sitting next to this file, so the server works no matter which
# directory the MCP client launches it from. Real env vars always win, which is
# what you want on a deployed host (Render/Fly inject them directly).
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ---------------------------------------------------------------------------
# 1. Config
# ---------------------------------------------------------------------------

MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
API_KEY = os.environ.get("GROQ_API_KEY")

if not API_KEY:
    print("ERROR: GROQ_API_KEY is not set.", file=sys.stderr)
    sys.exit(1)

client = Groq(api_key=API_KEY)

# The name shown to the MCP client.
mcp = MCPServer("llm-toolkit", version="1.0.0")


# ---------------------------------------------------------------------------
# 2. Shared helper - the one place that talks to the LLM
#
#    Swapping providers means editing THIS FUNCTION ONLY. The tools below never
#    know which model answered them. That separation is the point.
# ---------------------------------------------------------------------------

def call_llm(prompt: str, system: str = "", max_tokens: int = 1024) -> str:
    """Send a single-turn message to the model and return the text response."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_tokens=max_tokens,
        )
    except GroqError as exc:
        # Return the error as text so the MCP client can show it to the user
        # instead of the whole tool call blowing up.
        return f"Groq API error: {exc}"

    return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# 3. Tools - each @mcp.tool() becomes a callable tool in the MCP client
#    The docstring IS the tool description the model reads. Write it well.
# ---------------------------------------------------------------------------

@mcp.tool()
def ask_llm(question: str, style: str = "concise") -> str:
    """Ask the model a question and get an answer.

    Args:
        question: The question to ask.
        style: Answer style - "concise", "detailed", or "eli5".
    """
    styles = {
        "concise": "Answer in 3 sentences or fewer. No preamble.",
        "detailed": "Answer thoroughly with examples and edge cases.",
        "eli5": "Explain like the reader is five years old. Use simple analogies.",
    }
    system = styles.get(style, styles["concise"])
    return call_llm(question, system=system)


@mcp.tool()
def summarize(text: str, bullets: int = 5) -> str:
    """Summarize a block of text into a fixed number of bullet points.

    Args:
        text: The text to summarize.
        bullets: How many bullet points to produce (1-10).
    """
    bullets = max(1, min(bullets, 10))
    system = (
        f"Summarize the user's text into exactly {bullets} bullet points. "
        "Each bullet is one line. Output only the bullets, no heading."
    )
    return call_llm(text, system=system)


@mcp.tool()
def translate(text: str, target_language: str, tone: str = "neutral") -> str:
    """Translate text into a target language, preserving formatting.

    Args:
        text: The text to translate.
        target_language: e.g. "Hindi", "Spanish", "Japanese".
        tone: "neutral", "formal", or "casual".
    """
    system = (
        f"Translate the user's text into {target_language}. "
        f"Use a {tone} tone. Preserve markdown, line breaks, and code blocks. "
        "Output only the translation."
    )
    return call_llm(text, system=system)


@mcp.tool()
def extract_json(text: str, fields: str) -> str:
    """Pull structured JSON out of unstructured text.

    Args:
        text: The unstructured source text.
        fields: Comma-separated field names, e.g. "name,email,company".
    """
    system = (
        f"Extract these fields from the user's text: {fields}. "
        "Respond with a single valid JSON object and nothing else. "
        "Use null for any field you cannot find."
    )
    return call_llm(text, system=system)


# ---------------------------------------------------------------------------
# 4. A resource - read-only data the client can pull in as context
# ---------------------------------------------------------------------------

@mcp.resource("config://server-info")
def server_info() -> str:
    """Show which model this server is running."""
    return f"llm-toolkit MCP server\nprovider: groq\nmodel: {MODEL}\ntools: 4"


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
# 6. Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--http" in sys.argv:
        # Remote mode. Endpoint will be http://<host>:<port>/mcp
        mcp.run(
            transport="streamable-http",
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 8000)),
            stateless_http=True,  # no sticky sessions needed -> scales behind a LB
        )
    else:
        # Local mode. The client launches this process and talks over stdin/stdout.
        mcp.run(transport="stdio")
