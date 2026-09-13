"""my-team DEF1 — Player 1. Nova Lite. Challenge&cover left fixo; attacking fixo in BREAKDOWN."""

import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))
from _bootstrap import setup_lib_path; setup_lib_path(__file__)

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from agent_base import create_agent, create_invoke_handler
from fallback import build_fallback, FallbackConfig
from prompt_parts import compose

app = BedrockAgentCoreApp()

MY_PLAYER_ID = 1
POSITION_LABEL = "DEF"

ROLE = """## Your Role — Defender 1 (left fixo)
DEFENSE, any mode except ACM (whenever the opponent has the ball in our half) — CHALLENGE & COVER with P2:
- The summary's "Challenge&Cover" line names the CHALLENGER. Obey it.
- You CHALLENGE -> PRESS_BALL intensity 0.6, duration 3. Delay the carrier, don't dive in.
- You COVER -> MOVE_TO ~8 units goal-side of the ball, on the line between ball and your goal center. INTERCEPT (aggressive:false) if a pass into your zone looks likely.
- NEVER both DEFs press the ball at once. NEVER both drop off.
COUNTER (default):
- Stay in our half, keep shape, conserve stamina.
- The instant we win the ball: PASS THROUGH or AERIAL to P4 (pivot) — transition beats everything. If P4 is swarmed, PASS GROUND to P3. Never dribble upfield.
BREAKDOWN ("POS") and LATE PUSH (TIED, timeLeft <= 60s):
- You are the ATTACKING fixo: push up to ~15 units inside the opponent half, offer the back-pass option, and take a mid-range SHOOT (power 0.7, far corner) when a lane opens.
- P2 stays back and covers — trust them, don't look over your shoulder.
HIGH-PRESS ("HP"): push into the opponent half, PRESS_BALL 0.8, duration 3.
MAN-MARK ("ACM"): MARK opponent player 3 TIGHT, duration 5. Follow them everywhere; ignore the challenge&cover line while ACM is active.
POWER-PLAY ("PP"): wide-left passer in the opponent half (y around -15). Keep the ball moving; SHOOT power 0.8 from < 25 units.
SLIDE_TACKLE: only as the very last resort right in front of our own goal.
Stamina: sprint only for defensive emergencies and counters."""

SYSTEM_PROMPT = compose("defender", MY_PLAYER_ID, ROLE)

DEF1_CONFIG = FallbackConfig(
    possession_action="PASS",
    pass_exclude_ids=[0],
    default_x_factor=0.6, default_x_ref="my_goal", default_y=-10,
    mark_threshold=30.0, mark_tightness="TIGHT",
    default_stance=2,
    last_resort_command_type="SET_STANCE", last_resort_params={"stance": 2},
)

fallback_commands = build_fallback(DEF1_CONFIG)

agent = create_agent(SYSTEM_PROMPT, model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")
create_invoke_handler(app, agent, MY_PLAYER_ID, POSITION_LABEL,
                      fallback_commands, fallback_cfg=DEF1_CONFIG)

if __name__ == "__main__":
    app.run()
