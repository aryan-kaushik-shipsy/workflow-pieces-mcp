# Workflow Pieces MCP Server (Python)

A standalone MCP server that exposes workflow management as tools for Claude Code / Cursor.
Self-contained — can be moved out of the parent repo and run independently.

## Tools

| Tool | Description |
|------|-------------|
| `list_workflows` | List all registered workflows |
| `search_workflows` | Search workflows by keyword |
| `get_workflow_schema` | Get config/secretConfig fields required for a workflow |
| `list_identifiers` | List all client identifiers configured for a workflow |
| `get_config` | Get stored config for a workflow + identifier |
| `get_config_decrypted` | Get decrypted secrets (dev/local only) |
| `set_config` | Create or update config for a workflow |
| `trigger_workflow` | Trigger a workflow with a payload |

## Setup

### With uv (recommended)

```bash
cd mcp
pip install uv        # one-time, if not already installed
uv venv               # creates .venv/
uv pip install -r requirements.txt
cp .env.example .env  # fill in your values
```

### With standard venv (fallback)

```bash
cd mcp
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in your values
```

Edit `.env`:

```env
# Pick one auth method:
WP_AUTH_HEADER=Basic <base64-encoded-credentials>
# OR
WP_USERNAME=your-username
WP_PASSWORD=your-password

# Server to connect to:
WP_BASE_URL=http://localhost:3000

# Redis (for workflow list cache):
REDIS_URL=redis://localhost:6379
```

## Running

**stdio mode** — used by Claude Code and Cursor directly:

```bash
# with uv (no activate needed)
uv run python server.py

# with venv activated
python server.py
```

**SSE mode** — for remote/browser clients:

```bash
uv run fastmcp run server.py --transport sse --port 8000
```

## Connecting to Claude Code / Cursor

Add to your `.mcp.json`. You can run two servers side by side — one for local, one for prod.

**With uv** (`uv run` handles the venv automatically — no activation needed):

```json
{
  "mcpServers": {
    "workflow-pieces-local": {
      "command": "uv",
      "args": ["run", "python", "mcp/server.py"],
      "env": {
        "WP_BASE_URL": "http://localhost:3000",
        "WP_AUTH_HEADER": "Basic <local-token>",
        "REDIS_URL": "redis://localhost:6379"
      }
    },
    "workflow-pieces-prod": {
      "command": "uv",
      "args": ["run", "python", "mcp/server.py"],
      "env": {
        "WP_BASE_URL": "https://workflowpieces-gam.shipsy.io",
        "WP_AUTH_HEADER": "Basic <prod-token>",
        "REDIS_URL": "redis://localhost:6379"
      }
    }
  }
}
```

**With venv** (point directly to the venv Python binary):

```json
{
  "mcpServers": {
    "workflow-pieces-local": {
      "command": "mcp/.venv/bin/python",
      "args": ["mcp/server.py"],
      "env": {
        "WP_BASE_URL": "http://localhost:3000",
        "WP_AUTH_HEADER": "Basic <local-token>",
        "REDIS_URL": "redis://localhost:6379"
      }
    }
  }
}
```

> If you move the folder out of the repo, update `args` (and `command` for venv) to the new path.

## Moving out of the repo

The `mcp/` folder has zero dependencies on the parent TypeScript project. To move it:

```bash
cp -r mcp/ ~/wherever/workflow-pieces-mcp
cd ~/wherever/workflow-pieces-mcp
pip install -r requirements.txt
```

Update `args` in `.mcp.json` to point to the new path and restart the MCP server.
