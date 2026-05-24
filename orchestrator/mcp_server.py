"""
MCP Server — Spec v4 §12.1-12.2.

HTTP endpoint that exposes orchestrator tools to any MCP-speaking Claude
session. Runs in the same process as the orchestrator so it shares the
ArtifactStore, OPBacklog, Router, CycleManager, etc. — no IPC, no separate
process, no separate state. The Claude session connects over the network.

Authentication: bearer token (config.MCP_AUTH_TOKEN). The server rejects any
request without `Authorization: Bearer <token>` matching the configured value.
One token per deployment, matching the single-operator model (same as
Telegram's OP chat ID).

Mediation note (per spec §12.1): messages submitted via MCP may be paraphrased
by the mediating Claude session. The cycle archive records what was sent to
the agent, not what OP typed verbatim. For word-exact intent, OP can instruct
Claude "send exactly this: ..." or use Telegram (which preserves OP's literal
text).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from aiohttp import web

from artifact_store import ArtifactStore
from cycle_manager import CycleManager
from models import Artifact, PRIORITY_EMOJI
from op_backlog import OPBacklog
from sequence_manager import (
    InstanceManager, ModelAssignmentManager, SequenceManager,
)
from state_manager import load_json
from wiki_manager import WikiManager


# ─── Tool implementations ────────────────────────────────────────────────────

class MCPTools:
    """Concrete tool implementations. Calls these are 1:1 with the
    Telegram command set so both interfaces stay in sync (Spec v4 §12.2)."""

    def __init__(
        self,
        config,
        op_backlog: OPBacklog,
        store: ArtifactStore,
        cycles: CycleManager,
        wiki: WikiManager,
        instances: InstanceManager,
        models_mgr: ModelAssignmentManager,
        sequences: SequenceManager,
        sys_trigger: Callable[[Optional[str]], Awaitable[Any]],
        agent_retry: Callable[[str], Awaitable[Any]],
        agent_pause: Callable[[str], None],
        agent_resume: Callable[[str], None],
        process_disposition: Callable[..., Awaitable[str]],
        state_dir: str | Path,
        router: Any | None = None,
    ) -> None:
        self.config = config
        self.op_backlog = op_backlog
        self.store = store
        self.cycles = cycles
        self.wiki = wiki
        self.instances = instances
        self.models = models_mgr
        self.sequences = sequences
        self.sys_trigger = sys_trigger
        self.agent_retry = agent_retry
        self.agent_pause = agent_pause
        self.agent_resume = agent_resume
        self.process_disposition = process_disposition
        self.state_dir = Path(state_dir)
        self.router = router

    # ─── Status & monitoring ────────────────────────────────────────────────

    async def vega_status(self) -> dict[str, Any]:
        pending = self.op_backlog.list_pending()
        in_progress = self.op_backlog.list_in_progress()
        agents = {}
        for code in self.config.AGENTS:
            agents[code] = {
                "inbox_unprocessed": len(self.store.inbox(code).get_unprocessed()),
                "outbox_pending":    len(self.store.outbox(code).list_pending()),
                "instance":          self.instances.get_or_create(code),
                "model":             self.models.get(code),
            }
        return {
            "op_backlog": {"pending": len(pending), "in_progress": len(in_progress)},
            "agents": agents,
        }

    async def vega_backlog(self) -> list[dict[str, Any]]:
        items = self.op_backlog.list_pending() + self.op_backlog.list_in_progress()
        return [_artifact_dict(a) for a in items]

    async def vega_agent(self, code: str) -> dict[str, Any]:
        code = code.upper()
        if code not in self.config.AGENTS:
            return {"error": f"unknown agent: {code}"}
        inbox = self.store.inbox(code).get_unprocessed()
        outbox = self.store.outbox(code).list_pending()
        last_exec = _last_execution_for(self.state_dir, code)
        return {
            "code":             code,
            "instance":         self.instances.get_or_create(code),
            "model":            self.models.get(code),
            "inbox":            len(inbox),
            "outbox":           len(outbox),
            "last_execution":   last_exec,
        }

    async def vega_cycles(self) -> list[str]:
        return sorted(p.stem for p in self.cycles.active_dir.glob("*.json"))

    async def vega_history(self, artifact_id: str) -> list[dict[str, Any]]:
        log = load_json(self.state_dir / "routing_log.json", default=[])
        return [e for e in log if e.get("artifact_id") == artifact_id]

    # ─── Decision (Spec §11) ────────────────────────────────────────────────

    async def vega_approve(self, artifact_id: str) -> str:
        return await self.process_disposition(artifact_id, "approve", "")

    async def vega_reject(self, artifact_id: str, reason: str = "") -> str:
        return await self.process_disposition(artifact_id, "reject", reason)

    async def vega_modify(self, artifact_id: str, instructions: str = "") -> str:
        return await self.process_disposition(artifact_id, "modify", instructions)

    async def vega_exchange(self, artifact_id: str, message: str) -> str:
        """Multi-turn SG↔OP or SYS↔OP dialogue (Spec v4 §12.2).

        Mints PROP-OP-NNN, routes via router so the continuation turn is
        archived and shows up in routing_log. The agent picks up the active
        cycle via references field.
        """
        if self.router is None:
            return "router not wired"
        artifact_id_new = await self.sequences.next_id("OP", "PROP")
        msg = Artifact(
            type="PROP",
            sender="OP",
            recipient="SG",
            content=f"OP exchange message (re: {artifact_id}):\n\n{message}",
            references=[artifact_id],
            id=artifact_id_new,
        )
        await self.router.route(msg)
        return f"{artifact_id_new} forwarded to SG"

    async def vega_resolve(self, gov_id: str, action: str = "acknowledged") -> str:
        """Close an active GOV exchange (Spec v4 §6.1 + §12.2)."""
        path = self.op_backlog.find(gov_id)
        if not path:
            return f"No backlog item for {gov_id}"
        self.op_backlog.resolve(gov_id)
        self.cycles.close_by_artifact(gov_id)
        return f"{gov_id} resolved ({action}); gov_exchange cycle closed"

    # ─── Audit & governance ─────────────────────────────────────────────────

    async def vega_sys(self, instruction: str = "") -> dict[str, Any]:
        return await self.sys_trigger(instruction or None)

    async def vega_thinking(self, artifact_id: str) -> str:
        index = load_json(self.state_dir / "artifact_index.json", default={})
        record = index.get(artifact_id)
        if not record:
            return ""
        log = load_json(self.state_dir / "execution_log.json", default=[])
        if not isinstance(log, list):
            return ""
        target = record.get("execution_id")
        for e in reversed(log):
            if e.get("execution_id") == target:
                blocks = e.get("thinking_blocks", [])
                return "\n\n".join(blocks)
        return ""

    async def vega_wiki(self, code: str) -> dict[str, int]:
        code = code.upper()
        wiki_dir = Path(self.config.AGENTS_DIR) / code / "wiki"
        if not wiki_dir.exists():
            return {}
        return {p.name: p.stat().st_size for p in sorted(wiki_dir.glob("*.md"))}

    async def vega_log(self, code: str, n: int = 10) -> str:
        code = code.upper()
        path = Path(self.config.AGENTS_DIR) / code / "wiki" / "log.md"
        if not path.exists():
            return ""
        import re
        text = path.read_text()
        log_entry_re = re.compile(r"(?m)^## \[\d{4}-\d{2}-\d{2}")
        matches = list(log_entry_re.finditer(text))
        if not matches:
            return text[-3500:]
        offsets = [m.start() for m in matches] + [len(text)]
        entries = [text[offsets[i]:offsets[i + 1]] for i in range(len(matches))]
        return "".join(entries[-n:])

    # ─── Control ────────────────────────────────────────────────────────────

    async def vega_pause(self, code: str) -> str:
        self.agent_pause(code.upper())
        return f"paused {code.upper()}"

    async def vega_resume(self, code: str) -> str:
        self.agent_resume(code.upper())
        return f"resumed {code.upper()}"

    async def vega_rotate(self, code: str) -> str:
        new_id = await self.instances.rotate(code.upper())
        return new_id

    async def vega_model(self, code: str, model: str) -> str:
        await self.models.set(code.upper(), model)
        return f"{code.upper()} → {model}"

    # ─── Scope input + relay ────────────────────────────────────────────────

    async def vega_request(self, message: str) -> str:
        """Spec v4 §2.1 S13 — submit REQ-OP-NNN to SG."""
        if self.router is None:
            return "router not wired"
        artifact_id = await self.sequences.next_id("OP", "REQ")
        a = Artifact(type="REQ", sender="OP", content=message,
                     recipient="SG", id=artifact_id)
        await self.router.route(a)
        return f"{artifact_id} submitted to SG"

    async def vega_build(self, message: str) -> str:
        if self.router is None:
            return "router not wired"
        artifact_id = await self.sequences.next_id("EXT", "BRQ")
        a = Artifact(type="BRQ", sender="EXT", content=message,
                     recipient="BR", id=artifact_id)
        self.cycles.check_cycle_events(a, recipients=["BR"])
        await self.router.route(a)
        return f"{artifact_id} relayed to BR"

    async def vega_expert(self, message: str) -> str:
        if self.router is None:
            return "router not wired"
        artifact_id = await self.sequences.next_id("DE", "DE_IN")
        a = Artifact(type="DE_IN", sender="DE", content=message,
                     recipient="SG", id=artifact_id)
        self.cycles.check_cycle_events(a, recipients=["SG"])
        await self.router.route(a)
        return f"{artifact_id} relayed to SG"


# ─── Tool registry — MCP tool name → method name + JSON schema ──────────────

TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "vega_status":   {"method": "vega_status",   "params": {}},
    "vega_backlog":  {"method": "vega_backlog",  "params": {}},
    "vega_agent":    {"method": "vega_agent",    "params": {"code": "string"}},
    "vega_cycles":   {"method": "vega_cycles",   "params": {}},
    "vega_history":  {"method": "vega_history",  "params": {"artifact_id": "string"}},

    "vega_approve":  {"method": "vega_approve",  "params": {"artifact_id": "string"}},
    "vega_reject":   {"method": "vega_reject",   "params": {"artifact_id": "string",
                                                            "reason":      "string"}},
    "vega_modify":   {"method": "vega_modify",   "params": {"artifact_id":  "string",
                                                            "instructions": "string"}},
    "vega_exchange": {"method": "vega_exchange", "params": {"artifact_id": "string",
                                                            "message":     "string"}},
    "vega_resolve":  {"method": "vega_resolve",  "params": {"gov_id": "string",
                                                            "action": "string"}},

    "vega_sys":      {"method": "vega_sys",      "params": {"instruction": "string"}},
    "vega_thinking": {"method": "vega_thinking", "params": {"artifact_id": "string"}},
    "vega_wiki":     {"method": "vega_wiki",     "params": {"code": "string"}},
    "vega_log":      {"method": "vega_log",      "params": {"code": "string",
                                                            "n":    "integer"}},

    "vega_pause":    {"method": "vega_pause",    "params": {"code": "string"}},
    "vega_resume":   {"method": "vega_resume",   "params": {"code": "string"}},
    "vega_rotate":   {"method": "vega_rotate",   "params": {"code": "string"}},
    "vega_model":    {"method": "vega_model",    "params": {"code":  "string",
                                                            "model": "string"}},

    "vega_request":  {"method": "vega_request",  "params": {"message": "string"}},
    "vega_build":    {"method": "vega_build",    "params": {"message": "string"}},
    "vega_expert":   {"method": "vega_expert",   "params": {"message": "string"}},
}


# ─── HTTP server ─────────────────────────────────────────────────────────────

class MCPServer:
    """aiohttp-based MCP-style JSON-RPC server.

    Endpoints:
      GET  /health            — liveness probe (no auth)
      GET  /mcp/tools         — tool list (auth required)
      POST /mcp/call          — invoke {name, arguments} (auth required)

    The shape is "MCP-style" rather than strict MCP-over-stdio, because
    this orchestrator runs as a long-lived process and the OP's Claude
    session connects over HTTP. The tool schemas are equivalent to MCP
    tool definitions.
    """

    def __init__(self, config, tools: MCPTools) -> None:
        self.config = config
        self.tools = tools
        self._runner: web.AppRunner | None = None
        self._site: web.BaseSite | None = None

    def _auth_ok(self, request: web.Request) -> bool:
        token = getattr(self.config, "MCP_AUTH_TOKEN", "") or ""
        if not token:
            # No token configured — refuse all requests so a misconfigured
            # deployment doesn't silently expose tools.
            return False
        header = request.headers.get("Authorization", "")
        return header == f"Bearer {token}"

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _list_tools(self, request: web.Request) -> web.Response:
        if not self._auth_ok(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        tools = []
        for name, spec in TOOL_REGISTRY.items():
            tools.append({
                "name": name,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        k: {"type": v} for k, v in spec["params"].items()
                    },
                },
            })
        return web.json_response({"tools": tools})

    async def _call_tool(self, request: web.Request) -> web.Response:
        if not self._auth_ok(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid json"}, status=400)
        name = payload.get("name")
        args = payload.get("arguments", {}) or {}
        if name not in TOOL_REGISTRY:
            return web.json_response({"error": f"unknown tool: {name}"},
                                     status=404)
        method = getattr(self.tools, TOOL_REGISTRY[name]["method"])
        try:
            result = await method(**args)
        except TypeError as e:
            return web.json_response({"error": f"bad arguments: {e}"},
                                     status=400)
        except Exception as e:
            return web.json_response(
                {"error": f"{type(e).__name__}: {e}"}, status=500
            )
        return web.json_response({"result": result})

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/health", self._health)
        app.router.add_get("/mcp/tools", self._list_tools)
        app.router.add_post("/mcp/call", self._call_tool)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        host = getattr(self.config, "MCP_HOST", "127.0.0.1")
        port = getattr(self.config, "MCP_PORT", 8765)
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        print(f"[mcp] listening on http://{host}:{port}", flush=True)

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _artifact_dict(a: Artifact) -> dict[str, Any]:
    return {
        "id":        a.id,
        "type":      a.type,
        "sender":    a.sender,
        "recipient": a.recipient,
        "priority":  a.priority,
        "timestamp": a.timestamp,
        "emoji":     PRIORITY_EMOJI.get(a.priority or "P2", "🟡"),
    }


def _last_execution_for(state_dir: Path, agent_code: str) -> dict | None:
    log = load_json(state_dir / "execution_log.json", default=[])
    if not isinstance(log, list):
        return None
    for entry in reversed(log):
        if entry.get("agent") == agent_code:
            return {
                "execution_id":     entry.get("execution_id"),
                "timestamp":        entry.get("timestamp"),
                "duration_seconds": entry.get("duration_seconds"),
            }
    return None
