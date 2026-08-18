# Use the `llm-toolkit` MCP Server

Hand this page to anyone who wants the tools. Pick **one** of the three routes.

---

## Route 1 — Remote (nothing to install)

The server is already running in the cloud. You need no Python, no repo, no API key.

**Claude Code:**

```bash
claude mcp add --transport http llm-toolkit https://REPLACE-ME.onrender.com/mcp
```

**Claude Desktop / Cursor** — add to your MCP config file:

```json
{
  "mcpServers": {
    "llm-toolkit": {
      "type": "http",
      "url": "https://REPLACE-ME.onrender.com/mcp"
    }
  }
}
```

Config file locations:

- Claude Desktop, macOS — `~/Library/Application Support/Claude/claude_desktop_config.json`
- Claude Desktop, Windows — `%APPDATA%\Claude\claude_desktop_config.json`
- Cursor — `~/.cursor/mcp.json`

Restart the app fully after editing. The four tools appear under the tools icon.

> First call may take 30–50s if the free-tier server is asleep. That is normal, not a
> failure. Call it once to wake it, then it is fast.

---

## Route 2 — Local (your own key, your own machine)

Use this if you want to modify the tools, or you do not want to depend on someone
else's server.

```bash
git clone https://github.com/aihunter9892/mcpserver.git && cd mcpserver
```

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Windows: `.venv\Scripts\pip install -r requirements.txt`

Get a free key at [console.groq.com](https://console.groq.com), then:

```bash
cp .env.example .env
```

Edit `.env` and paste your key into `GROQ_API_KEY`. Then register the server:

```bash
claude mcp add llm-toolkit -- /absolute/path/to/mcpserver/.venv/bin/python /absolute/path/to/mcpserver/server.py
```

**Absolute paths matter.** The client launches this from its own working directory,
not yours. Relative paths are the #1 cause of "the server does not show up."

---

## Route 3 — Try it before wiring it up

The MCP Inspector shows the tool list and lets you call tools by hand. No AI client
needed, nothing registered.

```bash
npx @modelcontextprotocol/inspector python server.py
```

Open the printed URL, click **Connect**, then **List Tools**.

---

## What you get

| Tool           | Does                                            |
|----------------|-------------------------------------------------|
| `ask_llm`      | Ask a question — concise / detailed / eli5      |
| `summarize`    | Text into N bullet points                       |
| `translate`    | Translate, keeping markdown and code intact     |
| `extract_json` | Unstructured text into structured JSON          |

Plus a resource `config://server-info` (shows the live provider and model) and a
prompt `code_review`.

Try asking your client:

> Use extract_json to pull name, company and email out of this: "Priya leads design
> at Northwind, priya@northwind.co"

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Server not listed after editing config | App not fully restarted | Quit completely, reopen. Tray/menubar counts. |
| Works in Inspector, not in the client | Relative path | Use absolute paths for both python and `server.py`. |
| First call times out | Free-tier cold start | Curl the URL once to wake it, retry. |
| `404 model does not exist` | Provider retired that model ID | Set `GROQ_MODEL` to a current one from `client.models.list()`. |
| Browser shows 404 at the root URL | Not a bug | MCP lives at `/mcp`. There is no web page to view. |
| `429` errors | Free-tier quota spent | Wait for the window to reset, or use your own key via Route 2. |
