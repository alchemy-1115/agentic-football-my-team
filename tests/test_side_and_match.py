"""Tests for pitch-side detection, per-match conversation reset and payload logging.

Run with a Python that has strands-agents installed (same as test_local.py):
    python tests/test_side_and_match.py
No AWS calls.
"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "gateway_tools"))

from test_helpers import GAME_STATE, mock_agentcore_memory  # noqa: E402
from state import resolve_goal_positions, summarize_state  # noqa: E402
from fallback import FallbackConfig, MID_CONFIG, build_fallback  # noqa: E402
from agent_base import create_invoke_handler, resolve_team_id  # noqa: E402

MOVE = '[{"commandType":"MOVE_TO","playerId":3,"parameters":{"target_x":0,"target_y":0,"sprint":false},"duration":0}]'


def _copy(state):
    return json.loads(json.dumps(state))


def mirrored(state):
    """Same match with the pitch flipped: HOME now defends the RIGHT goal."""
    s = _copy(state)
    for p in s["players"]:
        p["position"]["x"] = -p["position"]["x"]
        p["position"]["y"] = -p["position"]["y"]
    s["ball"]["position"]["x"] = -s["ball"]["position"]["x"]
    s["ball"]["position"]["y"] = -s["ball"]["position"]["y"]
    return s


def test_side_from_goalkeepers():
    print("=== SIDE DETECTION ===")
    players = GAME_STATE["players"]  # mock: HOME GK at x=-50, AWAY GK at x=50
    assert resolve_goal_positions(players, 0) == (-55.0, 55.0)
    assert resolve_goal_positions(players, 1) == (55.0, -55.0)

    flipped = mirrored(GAME_STATE)["players"]
    assert resolve_goal_positions(flipped, 0) == (55.0, -55.0), "FAIL: HOME on the right not detected"
    assert resolve_goal_positions(flipped, 1) == (-55.0, 55.0), "FAIL: AWAY on the left not detected"

    close = _copy(GAME_STATE)["players"]
    for p in close:
        if p["agentId"] == "agentId_0":
            p["position"]["x"] = 5 if p["teamCode"] == "home" else -5
    assert resolve_goal_positions(close, 0) == (-55.0, 55.0), "FAIL: GKs too close must fall back to convention"
    print("  [OK] normal, mirrored, and too-close-to-call GK positions")
    print()


def test_summary_fallback_and_tools_follow_real_side():
    print("=== SUMMARY / FALLBACK / TOOLS ON A MIRRORED PITCH ===")
    flipped = mirrored(GAME_STATE)

    summary = summarize_state(flipped, 0, 4, "FWD1")
    assert "Your goal at x=55 | Opponent goal at x=-55" in summary, f"FAIL: summary side wrong:\n{summary}"

    loose = _copy(flipped)
    loose["ball"]["possessionAgentId"] = None
    fwd = build_fallback(FallbackConfig(possession_action="SHOOT_OR_ADVANCE",
                                        default_x_factor=0.4, default_x_ref="opp_goal"))
    target_x = fwd(loose, 0, 4)[0]["parameters"]["target_x"]
    assert target_x < 0, f"FAIL: fallback FWD must head for the LEFT (opponent) goal, got x={target_x}"

    import evaluate_shot
    import find_open_space
    args = {"game_state": flipped, "team_id": 0, "player_id": 3}
    shot = evaluate_shot.lambda_handler(args, None)
    assert shot["distance_to_goal"] == 41.3, f"FAIL: shot measured to the wrong goal: {shot}"
    space_x = find_open_space.lambda_handler({**args, "zone": "attack"}, None)["recommended_position"]["x"]
    assert -52 <= space_x <= -15, f"FAIL: attack zone on the wrong side: x={space_x}"
    print(f"  [OK] summary goal x=55, fallback target_x={target_x}, shot 41.3 to the left goal, space x={space_x}")
    print()


def test_team_id_resolution():
    print("=== TEAM ID RESOLUTION ===")
    assert resolve_team_id({"teamId": 1}) == (1, "teamId")
    assert resolve_team_id({"teamId": "1"}) == (1, "teamId")
    assert resolve_team_id({"teamCode": "away"}) == (1, "teamCode")
    assert resolve_team_id({}) == (0, "default")
    print("  [OK] int, numeric string, teamCode, missing")
    print()


class CaptureApp:
    """BedrockAgentCoreApp stand-in that records log lines."""

    def __init__(self):
        self.lines = []
        lines = self.lines

        class Logger:
            info = staticmethod(lambda msg: lines.append(("info", msg)))
            warn = staticmethod(lambda msg: lines.append(("warn", msg)))
            error = staticmethod(lambda msg: lines.append(("error", msg)))

        self.logger = Logger

    def entrypoint(self, fn):
        return fn


def _tick(invoke, game_time, with_team=True):
    state = _copy(GAME_STATE)
    state["gameTime"] = game_time
    prompt = {"gameState": state, "myPlayers": [3]}
    if with_team:
        prompt["teamId"] = 1

    async def run():
        return [out async for out in invoke({"prompt": json.dumps(prompt)}, SimpleNamespace(session_id="sess-1"))]

    return asyncio.run(run())


def test_new_match_resets_conversation_and_logs_payload():
    print("=== NEW MATCH RESET + PAYLOAD LOG ===")

    class PlainAgent:
        def __init__(self):
            self.messages = []

        def __call__(self, prompt):
            self.messages.append(prompt)
            return MOVE

    app, plain = CaptureApp(), PlainAgent()
    invoke = create_invoke_handler(app, plain, 3, "MID", build_fallback(MID_CONFIG), MID_CONFIG)
    _tick(invoke, 120)
    _tick(invoke, 122)
    assert len(plain.messages) == 2, "FAIL: history must carry over within a match"
    _tick(invoke, 1)  # gameTime rewound: a new match
    assert len(plain.messages) == 1, "FAIL: history not cleared on a new match"

    starts = [m for level, m in app.lines if "started" in m]
    assert len(starts) == 2, f"FAIL: expected 2 match-start logs, got {len(starts)}"
    shape = json.loads(starts[0].split("Payload shape: ", 1)[1])
    assert shape["teamId_present"] is True and shape["session_id"] == "sess-1", shape
    assert shape["players"] and "teamCode" in shape["players"][0], shape
    ticks = [m for level, m in app.lines if "agent invoked" in m]
    assert all("team 1 (teamId)" in m and "our goal x=55" in m for m in ticks), ticks

    class MemoryLikeAgent:
        def __init__(self):
            self.resets = 0

        def start_new_match(self):
            self.resets += 1

        def __call__(self, prompt):
            return MOVE

    app2, mem = CaptureApp(), MemoryLikeAgent()
    invoke2 = create_invoke_handler(app2, mem, 3, "MID", build_fallback(MID_CONFIG), MID_CONFIG)
    for t in (120, 122, 1, 3):
        _tick(invoke2, t, with_team=False)
    assert mem.resets == 2, f"FAIL: expected 2 new-session resets, got {mem.resets}"
    assert sum("no teamId" in m for level, m in app2.lines if level == "warn") == 2, app2.lines
    print("  [OK] history kept within a match, reset on rewind, payload shape + missing-teamId warning logged")
    print()


def test_memory_agent_new_session_per_match():
    print("=== MEMORY SESSION PER MATCH ===")
    mock_agentcore_memory()
    captured = []

    class CapturingSessionManager:
        def __init__(self, agentcore_memory_config, region_name=None, **kwargs):
            captured.append(agentcore_memory_config)

        def register_hooks(self, registry, **kwargs):
            pass

    sys.modules["bedrock_agentcore.memory.integrations.strands.session_manager"] \
        .AgentCoreMemorySessionManager = CapturingSessionManager

    saved = {k: os.environ.get(k) for k in ("MEMORY_ID", "AGENT_POSITION", "TEAM_ID")}
    os.environ.update(MEMORY_ID="mem-1", AGENT_POSITION="ai-mid", TEAM_ID="my-team")
    try:
        import memory_agent_base
        from strands import Agent

        agent = memory_agent_base.create_agent("system prompt", model_id="us.amazon.nova-micro-v1:0")
        assert agent.agent is None and not captured, "FAIL: no Memory session may be created at import time"
        agent.start_new_match()
        first = agent.agent
        agent.start_new_match()
        ids = [c.session_id for c in captured]
        assert len(set(ids)) == 2, f"FAIL: each match needs its own session id: {ids}"
        assert all(i.startswith("match-my-team-ai-mid-") for i in ids), ids
        assert isinstance(agent.agent, Agent) and agent.agent is not first, "FAIL: agent not rebuilt"

        del os.environ["MEMORY_ID"]
        assert isinstance(memory_agent_base.create_agent("system prompt"), Agent), \
            "FAIL: missing MEMORY_ID must fall back to a stateless Strands Agent"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print(f"  [OK] fresh session per match: {ids}")
    print()


if __name__ == "__main__":
    test_side_from_goalkeepers()
    test_summary_fallback_and_tools_follow_real_side()
    test_team_id_resolution()
    test_new_match_resets_conversation_and_logs_payload()
    test_memory_agent_new_session_per_match()
    print("Side / match tests passed.")
