"""Local tests for the tactical-tool Lambdas and the agent-side Gateway wrapper.

Run with a Python that has strands-agents + mcp installed (same as test_local.py):
    python gateway_tools/test_gateway.py
No AWS calls: the Lambdas run in-process and the MCP client is faked.
"""

import asyncio
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "lib"))

from test_helpers import GAME_STATE, TEAM_ID  # noqa: E402

with open(os.path.join(HERE, "tool_schemas.json"), encoding="utf-8") as f:
    SCHEMAS = json.load(f)

MID = 3  # our MID; in the mock state HOME P3 holds the ball at (14, -5)


def _call(module: str, **overrides) -> dict:
    """Invoke a tool with only the arguments its schema declares — what the Gateway passes."""
    entry = next(e for e in SCHEMAS if e["module"] == module)
    values = {"game_state": GAME_STATE, "team_id": TEAM_ID, "player_id": MID, "zone": "attack", **overrides}
    props = entry["tool"]["inputSchema"]["properties"]
    return importlib.import_module(module).lambda_handler({k: v for k, v in values.items() if k in props}, None)


def test_schemas_match_handlers():
    print("=== SCHEMA <-> HANDLER ===")
    for entry in SCHEMAS:
        schema = entry["tool"]["inputSchema"]
        for key in ("game_state", "team_id", "player_id"):
            assert key in schema["properties"] and key in schema["required"], \
                f"FAIL: {entry['module']} schema must declare and require {key}"
        result = _call(entry["module"])
        encoded = json.dumps(result)  # the Gateway needs a JSON-serialisable response
        assert "error" not in result, f"FAIL: {entry['module']} -> {result}"
        print(f"  [OK] {entry['tool']['name']}: {encoded[:90]}...")
    print()


def test_tool_results():
    print("=== TOOL RESULTS (HOME P3 holding the ball) ===")
    pass_ids = [o["player_id"] for o in _call("calculate_pass_options")["pass_options"]]
    assert sorted(pass_ids) == [0, 1, 2, 4], f"FAIL: pass targets must be every teammate but P3: {pass_ids}"

    shot = _call("evaluate_shot")
    assert shot["distance_to_goal"] == 41.3, f"FAIL: (14,-5) is 41.3 from (55,0): {shot}"

    threats = _call("get_defensive_assignment")["threats"]
    assert not any(t["has_ball"] for t in threats), \
        "FAIL: OUR P3 holds the ball — opponent P3 must not be flagged has_ball"

    home_x = _call("find_open_space")["recommended_position"]["x"]
    away_x = _call("find_open_space", team_id=1)["recommended_position"]["x"]
    assert 15 <= home_x <= 52 and -52 <= away_x <= -15, f"FAIL: attack zone x: home={home_x} away={away_x}"

    assert "error" in _call("evaluate_shot", player_id=9), "FAIL: unknown player must return an error"
    assert "error" in importlib.import_module("evaluate_shot").lambda_handler({}, None), \
        "FAIL: missing match context must return an error"
    print("  [OK] pass targets, shot distance, possession side, zones, input errors")
    print()


def test_tick_context_tool():
    print("=== GATEWAY WRAPPER ===")
    from mcp.types import Tool
    from strands.tools.mcp.mcp_agent_tool import MCPAgentTool
    from gateway_client import TickContext, TickContextTool, load_gateway_tools

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def call_tool_async(self, **kwargs):
            self.calls.append(kwargs)
            return {"toolUseId": kwargs["tool_use_id"], "status": "success", "content": [{"text": "{}"}]}

    entry = next(e for e in SCHEMAS if e["module"] == "find_open_space")
    gateway_name = f"{entry['target']}___{entry['tool']['name']}"
    mcp_tool = Tool(name=gateway_name, description=entry["tool"]["description"],
                    inputSchema=entry["tool"]["inputSchema"])
    client, tick = FakeClient(), TickContext()
    tool = TickContextTool(mcp_tool, client, tick)

    assert tool.tool_name == "find_open_space", f"FAIL: target prefix not stripped: {tool.tool_name}"
    schema = tool.tool_spec["inputSchema"]["json"]
    assert set(schema["properties"]) == {"zone"} and "required" not in schema, \
        f"FAIL: context args must be hidden from the LLM: {schema}"
    original = MCPAgentTool.tool_spec.fget(tool)["inputSchema"]["json"]
    assert "game_state" in original["properties"], "FAIL: hiding args mutated the underlying MCP schema"

    tick.update(GAME_STATE, TEAM_ID, MID)
    tool_use = {"toolUseId": "t1", "name": "find_open_space", "input": {"zone": "midfield", "player_id": 0}}
    before = json.dumps(tool_use)

    async def run():
        return [event async for event in tool.stream(tool_use, {})]

    asyncio.run(run())
    sent = client.calls[0]
    assert sent["name"] == gateway_name, f"FAIL: Gateway must get the full tool name: {sent['name']}"
    args = sent["arguments"]
    assert args["zone"] == "midfield", "FAIL: model-chosen argument dropped"
    assert args["player_id"] == MID and args["team_id"] == TEAM_ID and args["game_state"] is GAME_STATE, \
        "FAIL: tick context not attached (or model value not overridden)"
    assert json.dumps(tool_use) == before, "FAIL: tool_use in the conversation history was mutated"

    class QuietLog:
        info = staticmethod(lambda msg: None)
        error = staticmethod(lambda msg: None)

    saved = os.environ.pop("GATEWAY_URL", None)
    try:
        assert load_gateway_tools(TickContext(), log=QuietLog) == [], "FAIL: no GATEWAY_URL must mean no tools"
    finally:
        if saved is not None:
            os.environ["GATEWAY_URL"] = saved
    print("  [OK] name prefix stripped, context hidden + attached, history untouched, no-URL fallback")
    print()


if __name__ == "__main__":
    test_schemas_match_handlers()
    test_tool_results()
    test_tick_context_tool()
    print("Gateway tests passed.")
