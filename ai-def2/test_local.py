"""Local test for a my-team agent — state summary, coach-instruction wiring, fallback, parsing, optional LLM."""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from test_helpers import mock_agentcore_memory, GAME_STATE, TEAM_ID
os.environ.setdefault("MEMORY_ID", "test-memory-id")
os.environ.setdefault("TEAM_ID", str(TEAM_ID))
os.environ.setdefault("AGENT_POSITION", os.path.basename(os.path.dirname(os.path.abspath(__file__))))
mock_agentcore_memory()

from state import summarize_state
from parsing import parse_commands
from main import fallback_commands, MY_PLAYER_ID, POSITION_LABEL, SYSTEM_PROMPT


def state_with_coach():
    state = json.loads(json.dumps(GAME_STATE))
    state["teamChat"] = ["hold the line", "ACM"]
    return state


def test_summarize():
    print(f"=== STATE SUMMARY ({POSITION_LABEL}, player {MY_PLAYER_ID}) ===")
    summary = summarize_state(state_with_coach(), TEAM_ID, MY_PLAYER_ID, POSITION_LABEL)
    print(summary)
    assert "Match status:" in summary, "FAIL: pre-computed match status line missing"
    assert "Coach: ACM" in summary, "FAIL: teamChat coach instructions not in summary"
    # Stamina arrives as 0.0-1.0 — it must not collapse to stam=0/stam=1
    me = next(p for p in GAME_STATE["players"]
              if p["teamCode"] == "home" and p["agentId"].endswith(f"_{MY_PLAYER_ID}"))
    expected_stam = f"stam={me['stamina'] * 100:.0f}%"
    assert expected_stam in summary, f"FAIL: expected '{expected_stam}' in summary"
    # Open-goal flag: mock away GK sits 5 units off their goal; move them out to x=20
    assert "Opponent GK: distFromTheirGoal=5.0" in summary, "FAIL: opponent GK line missing"
    assert "OPEN GOAL" not in summary, "FAIL: OPEN GOAL flagged while GK is on their line"
    rushed = state_with_coach()
    next(p for p in rushed["players"]
         if p["teamCode"] == "away" and p["agentId"] == "agentId_0")["position"]["x"] = 20
    assert "OPEN GOAL" in summarize_state(rushed, TEAM_ID, MY_PLAYER_ID, POSITION_LABEL), \
        "FAIL: OPEN GOAL not flagged with opponent GK 35 units off their goal"
    if MY_PLAYER_ID in (1, 2):
        # C&C line is hidden during our own possession (mock state: our P3 has the ball)
        assert "Challenge&Cover" not in summary, "FAIL: C&C line shown during our own possession"
        loose = state_with_coach()
        loose["ball"]["possessionAgentId"] = None
        loose["ball"]["isFree"] = True
        loose_summary = summarize_state(loose, TEAM_ID, MY_PLAYER_ID, POSITION_LABEL)
        assert "Challenge&Cover" in loose_summary, "FAIL: C&C line missing when ball is loose"
    print("  [OK] match-status / coach lines present")
    print()


def test_prompt():
    print(f"=== PROMPT CHECK ({POSITION_LABEL}) ===")
    for token in ("MODE SYSTEM", "CTR", "POS", "HP", "ACM", "PP", "Response Format", f"player {MY_PLAYER_ID}"):
        assert token in SYSTEM_PROMPT, f"FAIL: '{token}' missing from system prompt"
    print(f"  [OK] all mode codewords present, prompt length {len(SYSTEM_PROMPT)} chars")
    print()


def test_fallback():
    print(f"=== FALLBACK ({POSITION_LABEL}) ===")
    cmds = fallback_commands(GAME_STATE, TEAM_ID, MY_PLAYER_ID)
    for c in cmds:
        print(f"  P{c.get('playerId')} T{c.get('teamId')}: {c['commandType']} {c.get('parameters', {})}")
    assert all(c["playerId"] == MY_PLAYER_ID for c in cmds), "FAIL: wrong playerId in fallback"
    # With the ball ourselves, the fallback must never pass to ourselves
    on_ball = json.loads(json.dumps(GAME_STATE))
    on_ball["ball"]["possessionAgentId"] = f"agentId_{MY_PLAYER_ID}"
    for c in fallback_commands(on_ball, TEAM_ID, MY_PLAYER_ID):
        print(f"  (on ball) P{c.get('playerId')}: {c['commandType']} {c.get('parameters', {})}")
        assert c.get("parameters", {}).get("target_player_id") != MY_PLAYER_ID, \
            "FAIL: fallback passes to itself"
    print("  [OK]")
    print()


def test_parse():
    print("=== PARSE TESTS ===")
    tests = [
        (f'[{{"commandType":"MOVE_TO","playerId":{MY_PLAYER_ID},"parameters":{{"target_x":-30,"target_y":2,"sprint":false}},"duration":0}}]', 1),
        ("invalid json", 0),
    ]
    for resp, expected in tests:
        cmds = parse_commands(resp, TEAM_ID, MY_PLAYER_ID)
        status = "PASS" if len(cmds) == expected else "FAIL"
        print(f"  [{status}] -> {len(cmds)} cmds (expected {expected})")
        assert len(cmds) == expected
    print()


def test_llm():
    print(f"=== LLM TEST ({POSITION_LABEL}) ===")
    try:
        from main import agent

        summary = summarize_state(state_with_coach(), TEAM_ID, MY_PLAYER_ID, POSITION_LABEL)
        print(f"Sending {len(summary)} chars (includes coach codeword 'ACM')...")
        response_text = str(agent(summary))
        print(f"Raw response: {response_text[:300]}")
        cmds = parse_commands(response_text, TEAM_ID, MY_PLAYER_ID)
        for c in cmds:
            print(f"  P{c.get('playerId')}: {c.get('commandType')} {c.get('parameters', {})}")
        print("LLM test PASSED" if cmds else "LLM test FAILED (no parseable command)")
    except Exception as e:
        print(f"LLM test error: {e}")
        print("(Make sure AWS credentials are set)")


if __name__ == "__main__":
    test_summarize()
    test_prompt()
    test_fallback()
    test_parse()
    if "--llm" in sys.argv:
        test_llm()
    else:
        print("Skipping LLM test. Run with --llm to test.")
