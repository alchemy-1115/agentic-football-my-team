"""AgentCore Gateway (MCP) tactical tools for my-team agents.

The tactical-tool Lambdas need the raw game state, but the LLM only ever sees
the text summary. So the tool specs shown to the LLM hide the match-context
arguments, and every call gets the current tick's values attached on the way
out. The model's own tool_use is never mutated, so the game state doesn't pile
up in the conversation history.
"""

import copy
import logging
import os
from typing import Any

from strands.tools.mcp.mcp_agent_tool import MCPAgentTool
from strands.tools.mcp.mcp_client import MCPClient

logger = logging.getLogger(__name__)

# Filled in by the runtime on every call — hidden from the LLM.
CONTEXT_ARGS = ("game_state", "team_id", "player_id")

# The Gateway exposes each tool as "<target>___<tool>".
_TARGET_DELIMITER = "___"


class TickContext:
    """Match context of the current tick, attached to every tool call."""

    def __init__(self) -> None:
        self.args: dict[str, Any] = {}

    def update(self, game_state: dict, team_id: int, player_id: int) -> None:
        self.args = {"game_state": game_state, "team_id": team_id, "player_id": player_id}


class TickContextTool(MCPAgentTool):
    """Gateway tool whose match-context arguments come from TickContext, not the LLM."""

    def __init__(self, mcp_tool, mcp_client: MCPClient, tick: TickContext) -> None:
        super().__init__(mcp_tool, mcp_client)
        self._tick = tick

    @property
    def tool_name(self) -> str:
        # Drop the "<target>___" prefix so prompts can name tools plainly. The
        # Gateway still gets the full name — MCPAgentTool calls it by mcp_tool.name.
        return self.mcp_tool.name.split(_TARGET_DELIMITER)[-1]

    @property
    def tool_spec(self):
        spec = copy.deepcopy(super().tool_spec)
        schema = spec["inputSchema"]["json"]
        for key in CONTEXT_ARGS:
            schema.get("properties", {}).pop(key, None)
        required = [r for r in schema.get("required", []) if r not in CONTEXT_ARGS]
        if required:
            schema["required"] = required
        else:
            schema.pop("required", None)
        return spec

    async def stream(self, tool_use, invocation_state, **kwargs):
        # Send a copy — the original tool_use is part of the conversation
        # history. Context values win over anything the model passed itself.
        model_input = tool_use.get("input")
        model_input = model_input if isinstance(model_input, dict) else {}
        call = {**tool_use, "input": {**model_input, **self._tick.args}}
        async for event in super().stream(call, invocation_state, **kwargs):
            yield event


def _transport(url: str):
    try:
        from mcp.client.streamable_http import streamable_http_client  # mcp 2.x
    except ImportError:
        from mcp.client.streamable_http import streamablehttp_client as streamable_http_client  # mcp 1.x
    return streamable_http_client(url)


def load_gateway_tools(tick: TickContext, log=logger) -> list[TickContextTool]:
    """Connect to the Gateway at GATEWAY_URL once and return its tools.

    The connection stays open for the life of the runtime: opening it per tick
    (`with mcp_client:`) adds a full MCP handshake to every decision. Returns []
    when GATEWAY_URL is unset or the Gateway is unreachable, so the agent still
    plays without tools instead of failing to start.
    """
    url = os.environ.get("GATEWAY_URL")
    if not url:
        log.info("GATEWAY_URL not set — running without tactical tools")
        return []

    client = MCPClient(lambda: _transport(url))
    try:
        client.start()
        tools = [TickContextTool(t.mcp_tool, client, tick) for t in client.list_tools_sync()]
    except Exception as e:
        log.error(f"Gateway tools unavailable, running without them: {e}")
        try:
            client.stop(None, None, None)
        except Exception:
            pass
        return []

    log.info(f"Loaded {len(tools)} Gateway tools: {[t.tool_name for t in tools]}")
    return tools
