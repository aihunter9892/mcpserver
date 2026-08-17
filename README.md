# Build Your Own MCP Server (and Deploy It)

A complete, working MCP server that turns an LLM API into MCP tools — built to be taught.
It runs **locally over stdio** for Claude Desktop / Claude Code, and **remotely over
HTTP** once you deploy it.

Backed by **Groq** — fast inference, OpenAI-compatible API, free tier that survives a
room full of students hammering it during a workshop.

Everything lives in one file: [`server.py`](server.py). ~170 lines including comments.

---

## Part 0 — What is MCP, in one minute

MCP (Model Context Protocol) is a standard way to give an AI client new abilities.
You write a server; any MCP client can use it.

A server can expose three things:

| Primitive     | What it is                              | Who controls it       |
|---------------|-----------------------------------------|-----------------------|
| **Tool**      | A function the model can call           | The model decides     |
| **Resource**  | Read-only data the client can pull in   | The client/app decides|
| **Prompt**    | A reusable prompt template              | The user picks it     |

Two transports:

- **stdio** — the client launches your server as a subprocess and talks over
  stdin/stdout. Local only. Zero networking. This is how 90% of MCP servers run.
- **streamable HTTP** — your server is a web service at a URL. This is what you
  deploy so other people (or hosted clients) can use it.

The same `server.py` does both. That's the whole trick.

---

## Part 1 — What we're building

`llm-toolkit`: an MCP server that gives any MCP client four LLM-powered tools.

| Tool           | Does                                              |
|----------------|---------------------------------------------------|
| `ask_llm`      | Ask a question, pick concise / detailed / eli5    |
| `summarize`    | Text → N bullet points                            |
| `translate`    | Translate, preserving markdown and code blocks    |
| `extract_json` | Unstructured text → structured JSON               |

Plus one resource (`config://server-info`) and one prompt (`code_review`) so students
see all three primitives.

---

## Part 2 — Run it locally

### Setup

```bash
python -m venv .venv
```

Windows: `.\.venv\Scripts\activate` — macOS/Linux: `source .venv/bin/activate`

```bash
pip install -r requirements.txt
```

Get a free key at [console.groq.com](https://console.groq.com) → API Keys. Then copy
`.env.example` to `.env` and paste it in:

```bash
cp .env.example .env
```

`.env` is gitignored. The server loads it automatically from its own directory, so it
works no matter where the client launches it from.

### Inspect it before wiring it up

The MCP Inspector is the single best teaching tool — it shows the tool list and lets
you call tools by hand, no AI client needed.

```bash
npx @modelcontextprotocol/inspector python server.py
```

Open the printed URL, hit **Connect**, then **List Tools**. You'll see all four.

---

## Part 3 — Connect it to a client

### Claude Code

```bash
claude mcp add llm-toolkit -e GROQ_API_KEY=gsk_... -- python /absolute/path/to/server.py
```

Or commit a `.mcp.json` in your project root so the whole team gets it — see
[`.mcp.json.example`](.mcp.json.example).

### Claude Desktop

Edit `claude_desktop_config.json`:

- macOS — `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows — `%APPDATA%\Claude\claude_desktop_config.json`

Paste the `mcpServers` block from `.mcp.json.example`, then **fully quit and reopen**
Claude Desktop. The tools appear under the tools icon.

> **Absolute paths only.** The #1 reason a local MCP server "doesn't show up" is a
> relative path — the client's working directory is not yours. Use the full path to
> both the python binary (`.venv/bin/python`) and `server.py`.

---

## Part 4 — Deploy it

Switch to HTTP mode with one flag:

```bash
python server.py --http
```

Server is now at `http://localhost:8000/mcp`. Ship that same command in a container.

### Option A — Render, no Docker (recommended)

Render has a **native Python runtime**. No Dockerfile, no container build. It installs
`requirements.txt` and runs your start command directly. This is the fastest path from
laptop to public URL.

**Step 1 — get the code on GitHub.**

```bash
git init && git add -A && git commit -m "MCP server"
```

Create an empty repo at [github.com/new](https://github.com/new), then:

```bash
git remote add origin https://github.com/<you>/llm-toolkit-mcp.git && git push -u origin main
```

**Step 2 — create the service.**

Render dashboard → **New → Web Service** → connect the repo. Render reads
[`render.yaml`](render.yaml) and configures itself:

| Setting       | Value                          |
|---------------|--------------------------------|
| Runtime       | Python (not Docker)            |
| Build command | `pip install -r requirements.txt` |
| Start command | `python server.py --http`      |

**Step 3 — set the key.** Dashboard → **Environment** → add `GROQ_API_KEY`. It's marked
`sync: false` in `render.yaml`, so it lives only in the dashboard, never in git.

**Step 4 — deploy.** Your public endpoint is `https://<your-app>.onrender.com/mcp`.

> **No health check on purpose.** `GET /mcp` opens an SSE stream that stays open by
> design. A health check pointed at it hangs, and Render reads the timeout as a dead
> service and restart-loops it. With `healthCheckPath` omitted, Render just verifies
> the process binds `$PORT` — the correct check for this server.

> Free-tier instances sleep after ~15 min idle. The first call after a sleep takes
> ~30–50s while it wakes. Some MCP clients time out before that and report the server
> as broken. Warm it with a curl before class starts.

### Option B — Other no-Docker hosts

| Host                    | How                                                        |
|-------------------------|------------------------------------------------------------|
| **Railway**             | Connect repo. Nixpacks auto-detects Python. Set start command to `python server.py --http`. |
| **Hugging Face Spaces** | Free, no sleep. Docker Space, or Gradio Space with a custom `app.py` shim. |
| **Google Cloud Run**    | `gcloud run deploy --source .` — builds from source, no Dockerfile needed. |
| **Any VPS**             | `pip install -r requirements.txt`, then run under systemd or `tmux`. |

### Option C — Fly.io

```bash
fly launch --no-deploy
```

```bash
fly secrets set GROQ_API_KEY=gsk_...
```

```bash
fly deploy
```

Endpoint: `https://<your-app>.fly.dev/mcp`

### Option D — Any container host

The [`Dockerfile`](Dockerfile) is kept for hosts that want a container. Works on
Railway, Cloud Run, ECS, a VPS:

```bash
docker build -t llm-toolkit-mcp .
```

```bash
docker run -p 8000:8000 -e GROQ_API_KEY=gsk_... llm-toolkit-mcp
```

### Verify the deployment

One curl proves the server is alive and speaking MCP:

```bash
curl -X POST https://your-app.onrender.com/mcp -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

You should get back a `serverInfo` block naming `llm-toolkit`.

### Connect a client to the deployed server

```bash
claude mcp add --transport http llm-toolkit https://your-app.onrender.com/mcp
```

Students paste that one line and instantly have your four tools. That's the payoff
moment of the whole workshop — no install, no key, no Python on their machine.

### Sharing it publicly — read this first

A deployed MCP server with no auth is **open to the entire internet**. Anyone who
learns the URL can call your tools, and every call spends *your* Groq quota.

For a workshop that is usually fine, and Groq's free tier is what makes it fine: when
the quota runs out you get HTTP 429 errors, not a bill. The failure mode is "tools stop
answering," not "surprise invoice."

It stops being fine the moment you put a **paid** key behind it. Then add auth before
sharing the URL — the MCP SDK's `auth` parameter, or an API gateway in front.

Two habits worth keeping either way:

- Treat the URL as semi-secret. Share it in class, don't post it publicly.
- Rotate the key after the workshop. It's one dashboard click.

## Part 5 — Things worth teaching explicitly

**The docstring is the API.** The model picks tools by reading the docstring and the
type hints. A vague docstring means a tool that never gets called. This is the single
highest-leverage thing in the whole file.

**One function owns the provider.** Every tool calls `call_llm()`. Swapping Groq for
OpenAI, Anthropic, or a local Ollama means editing that one function — the four tools
never change. Demo this live; it lands hard.

**Return errors as strings, don't raise.** `call_llm` catches `GroqError` and returns
the message as text. The client shows the user a real error instead of a dead tool call.

**`stateless_http=True`** means no sticky sessions, so the server scales behind a load
balancer. Turn it off only if you add per-session state.

**Never commit the key.** `.env` is gitignored, `render.yaml` uses `sync: false`,
Fly uses `fly secrets`.

**Public HTTP servers are open by default.** This one has no auth — fine for a demo,
not for production. Real deployments add OAuth via the SDK's `auth` parameter, or sit
behind an API gateway.

**Version drift is real.** MCP Python SDK 2.0 renamed `FastMCP` to `MCPServer`. Most
tutorials online still show `FastMCP` and will fail on a fresh install. Good moment to
teach reading the installed package instead of trusting a blog post.

---

## Part 6 — Exercises for the class

1. Add a `sentiment(text)` tool. (Copy `summarize`, change the system prompt.)
2. Make `ask_llm` accept a `max_tokens` argument and watch the schema update
   automatically in the Inspector.
3. Point `call_llm` at a different provider without touching any tool.
4. Add a resource `config://usage` that reports how many tool calls the process has
   served. (Hint: a module-level counter.)
5. Break a docstring on purpose, then ask the model to use that tool. Watch it fail to
   pick the tool. That's the lesson.

---

## File map

| File                 | Why it exists                                  |
|----------------------|------------------------------------------------|
| `server.py`          | The entire server — tools, resource, prompt    |
| `requirements.txt`   | `mcp[cli]` + `groq` + `python-dotenv`          |
| `Dockerfile`         | Container for any host                         |
| `render.yaml`        | One-click Render deploy                        |
| `fly.toml`           | Fly.io deploy                                  |
| `.env.example`       | Which env vars exist                           |
| `.mcp.json.example`  | Client config to copy                          |
