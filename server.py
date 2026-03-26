#!/usr/bin/env python3
"""
Workflow Pieces MCP Server (Python / FastMCP)

Self-contained — no dependencies on the parent TypeScript project.
Can be moved out of this repo and run independently.

Environment variables:
  WP_AUTH_HEADER  — full Authorization header, e.g. "Basic <base64>"  (preferred)
  WP_USERNAME     — dashboard username, used with WP_PASSWORD as a fallback
  WP_PASSWORD     — dashboard password, used with WP_USERNAME as a fallback
  WP_BASE_URL     — workflow-pieces server URL (default: http://localhost:3000)
  REDIS_URL       — Redis connection URL (default: redis://localhost:6379)

Run (stdio, for Claude Code / Cursor):
  python server.py

Run (SSE, for browser / remote clients):
  fastmcp run server.py --transport sse --port 8000
"""

import base64
import json
import os
import sys
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from rapidfuzz import fuzz

from dotenv import load_dotenv
load_dotenv()

import httpx
import redis.asyncio as aioredis
from mcp.server.fastmcp import FastMCP

# ─── Config ───────────────────────────────────────────────────────────────────

BASE_URL = os.getenv("WP_BASE_URL", "http://localhost:3000").rstrip("/")
ENCRYPTION_KEY = os.getenv("WP_ENCRYPTION_KEY", "3NoHR8z7BfxG4EIQUnjTzvZ6h5WCog1S").encode()

if auth_header := os.getenv("WP_AUTH_HEADER"):
    DASHBOARD_AUTH = auth_header
elif (username := os.getenv("WP_USERNAME")) and (password := os.getenv("WP_PASSWORD")):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    DASHBOARD_AUTH = f"Basic {token}"
else:
    sys.stderr.write("Set either WP_AUTH_HEADER or both WP_USERNAME and WP_PASSWORD\n")
    sys.exit(1)

DASHBOARD_HEADERS = {
    "Authorization": DASHBOARD_AUTH,
    "Content-Type": "application/json",
}

# ─── Redis cache ──────────────────────────────────────────────────────────────

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
WORKFLOW_CACHE_KEY = "mcp:workflow-registry"
WORKFLOW_CACHE_TTL = 300  # 5 minutes

redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def build_url(base_path: str, workflow_url: str, identifier: Optional[str] = None) -> str:
    """Appends the identifier segment when present: /base/path[/identifier]"""
    return f"{base_path}{workflow_url}/{identifier}" if identifier else f"{base_path}{workflow_url}"


def _decrypt_field(encrypted_string: str) -> object:
    """Decrypt an AES-256-GCM value stored as ivHex:authTagBase64:encryptedBase64."""
    iv_hex, auth_tag_b64, encrypted_b64 = encrypted_string.strip().split(":")
    iv = bytes.fromhex(iv_hex)
    auth_tag = base64.b64decode(auth_tag_b64)
    ciphertext = base64.b64decode(encrypted_b64)
    aesgcm = AESGCM(ENCRYPTION_KEY)
    # cryptography expects ciphertext with the 16-byte tag appended
    decrypted = aesgcm.decrypt(iv, ciphertext + auth_tag, None)
    return json.loads(decrypted.decode("utf-8"))


def try_decrypt(value: Optional[str]) -> object:
    if not value:
        return None
    try:
        return _decrypt_field(value)
    except Exception:
        # Value may be stored as plain-text JSON — try to parse it directly
        try:
            return json.loads(value)
        except Exception:
            return {"_decryption_failed": True, "raw": value}


async def get_workflows() -> list[dict]:
    cached = await redis_client.get(WORKFLOW_CACHE_KEY)
    if cached:
        return json.loads(cached)

    async with httpx.AsyncClient(headers=DASHBOARD_HEADERS, timeout=30) as client:
        res = await client.get(f"{BASE_URL}/api/workflows")
        res.raise_for_status()
        workflows: list[dict] = res.json()["data"]

    await redis_client.setex(WORKFLOW_CACHE_KEY, WORKFLOW_CACHE_TTL, json.dumps(workflows))
    return workflows


async def resolve_workflow(workflow_id: str) -> dict:
    workflows = await get_workflows()
    found = next((w for w in workflows if w["id"] == workflow_id), None)
    if not found:
        # Invalidate and retry once in case a new workflow was deployed since last cache fill
        await redis_client.delete(WORKFLOW_CACHE_KEY)
        workflows = await get_workflows()
        found = next((w for w in workflows if w["id"] == workflow_id), None)
        if not found:
            raise ValueError(
                f"Workflow '{workflow_id}' not found. "
                "Use list_workflows or search_workflows to find the correct id."
            )
    return found

# ─── MCP server ───────────────────────────────────────────────────────────────

mcp = FastMCP("workflow-pieces")


@mcp.tool()
async def list_workflows() -> str:
    """List all registered workflows with their id, url, HTTP method, and execution mode."""
    workflows = await get_workflows()
    return json.dumps(workflows, indent=2)


SEARCH_SCORE_THRESHOLD = 60  # 0–100; lower = more permissive

@mcp.tool()
async def search_workflows(q: str) -> str:
    """
    Search workflows by keyword using fuzzy matching.
    Scores each workflow's id and url against the query, returns results ranked by relevance.
    Handles partial matches, typos, and hyphenated names.
    """
    workflows = await get_workflows()
    q_lower = q.lower().strip()

    scored = []
    for w in workflows:
        score = max(
            fuzz.partial_ratio(q_lower, w["id"].lower()),
            fuzz.partial_ratio(q_lower, w["url"].lower()),
            fuzz.token_set_ratio(q_lower, w["id"].lower()),
        )
        if score >= SEARCH_SCORE_THRESHOLD:
            scored.append((score, w))

    if not scored:
        return f'No workflows found matching "{q}"'

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [
        {**w, "_score": score}
        for score, w in scored
    ]
    return json.dumps(results, indent=2)


@mcp.tool()
async def get_workflow_schema(workflow_id: str) -> str:
    """
    Get the full schema for a workflow — config fields, secretConfig fields,
    auth type, execution mode, and HTTP method.
    Use this before set_config to know what fields are required.
    """
    workflow = await resolve_workflow(workflow_id)
    async with httpx.AsyncClient(headers=DASHBOARD_HEADERS, timeout=30) as client:
        res = await client.get(f"{BASE_URL}/api/workflow{workflow['url']}")
        res.raise_for_status()
    return json.dumps(res.json()["data"], indent=2)


@mcp.tool()
async def list_identifiers(workflow_id: str) -> str:
    """
    List all identifiers (client configs) configured for a workflow.
    Each identifier represents a separate client/environment setup.
    """
    workflow = await resolve_workflow(workflow_id)
    async with httpx.AsyncClient(headers=DASHBOARD_HEADERS, timeout=30) as client:
        res = await client.get(f"{BASE_URL}/api/config{workflow['url']}")
        res.raise_for_status()
        configs: list[dict] = res.json()["data"]

    if not configs:
        return f"No configs found for workflow '{workflow_id}'"

    identifiers = [
        {
            "identifier": c["identifier"] or "(default — no identifier)",
            "updated_at": c["updated_at"],
            "updated_by": c["updated_by"],
        }
        for c in configs
    ]
    return json.dumps(identifiers, indent=2)


@mcp.tool()
async def get_config(workflow_id: str, identifier: Optional[str] = None) -> str:
    """
    Get the stored config for a workflow and optional identifier.
    Returns authcredentials and config.
    secret_config is intentionally hidden — use get_config_decrypted if you need the actual secret values.
    """
    workflow = await resolve_workflow(workflow_id)
    url = build_url("/api/config", workflow["url"], identifier)
    async with httpx.AsyncClient(headers=DASHBOARD_HEADERS, timeout=30) as client:
        res = await client.get(f"{BASE_URL}{url}")
        res.raise_for_status()
    return json.dumps(res.json()["data"], indent=2)


@mcp.tool()
async def get_config_decrypted(workflow_id: str, identifier: Optional[str] = None) -> str:
    """
    Get fully decrypted config including secret values (API keys, passwords) for a workflow.
    Decryption happens locally in the MCP server using WP_ENCRYPTION_KEY — no backend
    dev-only route required, works in all environments.
    """
    workflow = await resolve_workflow(workflow_id)
    async with httpx.AsyncClient(headers=DASHBOARD_HEADERS, timeout=30) as client:
        res = await client.get(f"{BASE_URL}/api/config{workflow['url']}")
        res.raise_for_status()
        configs: list[dict] = res.json()["data"]

    if not configs:
        return f"No configs found for workflow '{workflow_id}'"

    # Match by identifier (None == default config)
    match = next(
        (c for c in configs if c.get("identifier") == identifier),
        None,
    )
    if not match:
        msg = (
            f"No config found for workflow '{workflow_id}' with identifier '{identifier}'"
            if identifier
            else f"No default config found for workflow '{workflow_id}'"
        )
        return msg

    return json.dumps(
        {
            "workflowId": workflow_id,
            "identifier": match.get("identifier"),
            "updated_at": match.get("updated_at"),
            "updated_by": match.get("updated_by"),
            "authcredentials": try_decrypt(match.get("authcredentials")),
            "config": try_decrypt(match.get("config")),
            "secret_config": try_decrypt(match.get("secret_config")),
        },
        indent=2,
    )


@mcp.tool()
async def set_config(
    workflow_id: str,
    authcredentials: dict,
    config: dict,
    identifier: Optional[str] = None,
    secret_config: Optional[dict] = None,
) -> str:
    """
    Create or update the config for a workflow. Automatically handles POST vs PUT.
    Validates that all required config and secretConfig fields are present before calling the API.

    authcredentials format:
      Basic auth:  {"type": "basic", "username": "...", "password": "...", "whitelistedIps": []}
      API key:     {"type": "apikey", "key": "...", "whitelistedIps": []}
    """
    workflow = await resolve_workflow(workflow_id)

    async with httpx.AsyncClient(headers=DASHBOARD_HEADERS, timeout=30) as client:
        schema_res = await client.get(f"{BASE_URL}/api/workflow{workflow['url']}")
        schema_res.raise_for_status()
        schema: dict = schema_res.json()["data"]

    missing_config = [
        f["name"]
        for f in (schema.get("config") or [])
        if f.get("required") and f["name"] not in config
    ]
    missing_secret = [
        f["name"]
        for f in (schema.get("secretConfig") or [])
        if f.get("required") and (not secret_config or f["name"] not in secret_config)
    ]

    if missing_config or missing_secret:
        lines = ["Missing required fields:"]
        if missing_config:
            lines.append(f"  config: {', '.join(missing_config)}")
        if missing_secret:
            lines.append(f"  secretConfig: {', '.join(missing_secret)}")
        lines += ["", "Full schema:", json.dumps({"config": schema.get("config"), "secretConfig": schema.get("secretConfig")}, indent=2)]
        return "\n".join(lines)

    config_url = build_url("/api/config", workflow["url"], identifier)

    # Check if config exists to decide POST vs PUT
    existing = None
    try:
        async with httpx.AsyncClient(headers=DASHBOARD_HEADERS, timeout=30) as client:
            check_res = await client.get(f"{BASE_URL}{config_url}")
            check_res.raise_for_status()
            configs = check_res.json()["data"]
        if isinstance(configs, list):
            existing = next(
                (c for c in configs if c["identifier"] == (identifier or None)),
                None,
            )
    except Exception:
        pass  # 404 — no config yet, proceed with POST

    body: dict = {"authcredentials": authcredentials, "config": config}
    if secret_config:
        body["secretConfig"] = secret_config

    async with httpx.AsyncClient(headers=DASHBOARD_HEADERS, timeout=30) as client:
        if existing:
            res = await client.put(f"{BASE_URL}{config_url}", json=body)
        else:
            res = await client.post(f"{BASE_URL}{config_url}", json=body)
        res.raise_for_status()

    action = "updated" if existing else "created"
    return f"Config {action} successfully.\n\n{json.dumps(res.json(), indent=2)}"


@mcp.tool()
async def trigger_workflow(
    workflow_id: str,
    payload: dict,
    identifier: Optional[str] = None,
) -> str:
    """
    Trigger a workflow with a payload.
    Automatically fetches stored auth credentials and uses them.
    Falls back to no auth if the workflow uses skipAuth.
    """
    workflow = await resolve_workflow(workflow_id)
    webhook_url = build_url("/api/webhook", workflow["url"], identifier)

    # Use a separate client (no DASHBOARD_HEADERS) — dashboard auth must not
    # be forwarded to workflow webhook endpoints.
    webhook_headers: dict[str, str] = {"Content-Type": "application/json"}

    try:
        # Regular config endpoint works on both dev and prod
        # (trigger only needs authcredentials, not secretConfig)
        async with httpx.AsyncClient(headers=DASHBOARD_HEADERS, timeout=30) as client:
            config_res = await client.get(f"{BASE_URL}/api/config{workflow['url']}")
            config_res.raise_for_status()
            configs: list[dict] = config_res.json()["data"]

        match = next(
            (c for c in (configs or []) if c["identifier"] == (identifier or None)),
            None,
        )
        stored: dict = (match or {}).get("authcredentials") or {}

        if stored.get("type") == "basic":
            token = base64.b64encode(
                f"{stored['username']}:{stored['password']}".encode()
            ).decode()
            webhook_headers["Authorization"] = f"Basic {token}"
        elif stored.get("type") == "apikey":
            webhook_headers["x-api-key"] = stored["key"]
    except Exception:
        pass  # No stored config — proceed without auth (workflow may use skipAuth)

    method = workflow["method"].lower()
    async with httpx.AsyncClient(timeout=60) as client:
        res = await getattr(client, method)(
            f"{BASE_URL}{webhook_url}",
            json=payload,
            headers=webhook_headers,
        )
        res.raise_for_status()

    return (
        f"Workflow triggered successfully.\n\n"
        f"Status: {res.status_code}\n\n"
        f"Response:\n{json.dumps(res.json(), indent=2)}"
    )


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
