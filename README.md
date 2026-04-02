# Workflow Pieces MCP Server (Python)

A standalone MCP server that exposes workflow management as tools for Claude Code.
Self-contained — can be moved out of the parent repo and run independently.

---

## Tools

| Tool | Description |
|------|-------------|
| `list_workflows` | List all registered workflows |
| `search_workflows` | Search workflows by keyword (fuzzy) |
| `get_workflow_schema` | Get config/secretConfig fields required for a workflow |
| `list_identifiers` | List all client identifiers configured for a workflow |
| `get_config` | Get stored config for a workflow + identifier |
| `get_config_decrypted` | Get decrypted secrets — API or direct DB |
| `get_config_decrypted_from_db` | Fetch and decrypt credentials directly from the database |
| `set_config` | Create or update config for a workflow |
| `trigger_workflow` | Trigger a workflow with a payload |

### Tool flags

| Flag | Applies to | Default | Effect |
|------|-----------|---------|--------|
| `skip_cache` | `list_workflows`, `search_workflows`, `get_config_decrypted` | `false` | Bypass Redis, hit API fresh |
| `use_db` | `get_config_decrypted` | `false` | Fetch directly from DB instead of API |

---

## Quick Setup

### 1. Install dependencies

```bash
cd mcp
pip install uv        # one-time, if not already installed
uv sync               # installs all deps from pyproject.toml into .venv/
```

### 2. Configure environment

Copy the example and fill in your values:

```bash
cp .env.example .env
```

`.env` variables:

```env
# Pick one auth method:
WP_AUTH_HEADER=Basic <base64-encoded-credentials>
# OR
WP_USERNAME=your-username
WP_PASSWORD=your-password

# Required — no default, server exits if missing:
WP_ENCRYPTION_KEY=<your-32-char-encryption-key>

# Optional — these have localhost defaults:
WP_BASE_URL=http://localhost:3000
REDIS_URL=redis://localhost:6379

# Optional — only needed if using use_db=true on get_config_decrypted:
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

> **Generate a `WP_AUTH_HEADER`:** `echo -n "username:password" | base64` then prefix with `Basic `.

### 3. Set up `.mcp.json`

The `.mcp.json` file tells Claude Code how to start the MCP server. It is **gitignored** — each developer keeps their own copy with their own credentials.

Copy the example:

```bash
cp .mcp.json.example .mcp.json
```

Then edit `.mcp.json` and fill in your values:

```json
{
  "mcpServers": {
    "workflow-pieces": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/absolute/path/to/mcp",
        "python",
        "/absolute/path/to/mcp/server.py"
      ],
      "env": {
        "WP_BASE_URL": "http://localhost:3000",
        "WP_AUTH_HEADER": "Basic <your-token>",
        "WP_ENCRYPTION_KEY": "<your-32-char-key>",
        "REDIS_URL": "redis://localhost:6379",
        "DATABASE_URL": "postgresql://user:password@host:5432/dbname"
      }
    }
  }
}
```

> Use **absolute paths** in `args`. `uv run --project` ensures the right virtualenv is used regardless of where you open your terminal.

---

## Connecting to Claude Code

### Where to place `.mcp.json`

`.mcp.json` is picked up automatically by Claude Code based on where it is placed:

| Location | Scope |
|----------|-------|
| Inside a project directory | Available only when Claude Code is opened in that directory |
| `~/.claude/.mcp.json` (global) | Available in every Claude Code session |

**Recommended:** place `.mcp.json` in the root of the project where you want to use it (e.g. the `workflow-pieces` repo root or any other project you work in). Claude Code will pick it up when you open that folder.

### Open with integrated terminal (VS Code / Cursor)

1. Open the project folder in VS Code or Cursor: `File → Open Folder → /path/to/your/project`
2. Open the integrated terminal: `` Ctrl+` `` (macOS: `` Cmd+` ``)
3. Claude Code reads `.mcp.json` from the working directory automatically — no extra steps needed.

---

## Managing the MCP connection in Claude Code

Use the `/mcp` slash command inside Claude Code to manage servers:

```
/mcp                    # show all connected MCP servers and their status
/mcp restart            # restart all MCP servers (picks up code/config changes)
/mcp restart workflow-pieces   # restart a specific server by name
```

### Common workflows

**After editing `server.py`** — restart the server so Claude picks up the new tools:
```
/mcp restart workflow-pieces
```

**After editing `.mcp.json`** (changed credentials, paths, env vars):
```
/mcp restart workflow-pieces
```

**Check if connected:**
```
/mcp
```
You should see `workflow-pieces` listed with status `connected`. If it shows `error`, check that:
- Paths in `.mcp.json` are absolute and correct
- `uv` is installed and on your PATH
- All required env vars (`WP_AUTH_HEADER`, `WP_ENCRYPTION_KEY`) are set in the `env` block

**Server not appearing at all:** Claude Code only reads `.mcp.json` on startup. If you added the file after opening Claude Code, restart Claude Code entirely.

---

## Running manually (without Claude Code)

**stdio mode** — same as what Claude Code uses:

```bash
uv run --project /path/to/mcp python /path/to/mcp/server.py
```

**SSE mode** — for remote/browser clients:

```bash
uv run fastmcp run server.py --transport sse --port 8000
```

---

## Multiple environments (local + prod)

You can run two server instances side by side in `.mcp.json`:

```json
{
  "mcpServers": {
    "workflow-pieces-local": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/mcp", "python", "/path/to/mcp/server.py"],
      "env": {
        "WP_BASE_URL": "http://localhost:3000",
        "WP_AUTH_HEADER": "Basic <local-token>",
        "WP_ENCRYPTION_KEY": "<key>",
        "REDIS_URL": "redis://localhost:6379"
      }
    },
    "workflow-pieces-prod": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/mcp", "python", "/path/to/mcp/server.py"],
      "env": {
        "WP_BASE_URL": "https://your-prod-url.example.com",
        "WP_AUTH_HEADER": "Basic <prod-token>",
        "WP_ENCRYPTION_KEY": "<key>",
        "REDIS_URL": "redis://localhost:6379"
      }
    }
  }
}
```

---

## Moving out of the repo

This folder has zero dependencies on the parent project. To move it:

```bash
cp -r mcp/ ~/wherever/workflow-pieces-mcp
cd ~/wherever/workflow-pieces-mcp
uv sync
```

Update the paths in your `.mcp.json` to point to the new location and run `/mcp restart`.
