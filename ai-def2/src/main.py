"""my-team DEF2 — Player 2. Nova Lite. Challenge&cover right fixo; staying fixo (rest defense)."""

import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))
from _bootstrap import setup_lib_path; setup_lib_path(__file__)

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from agent_base import create_agent, create_invoke_handler
from fallback import build_fallback, FallbackConfig
from prompt_parts import compose

app = BedrockAgentCoreApp()

MY_PLAYER_ID = 2
POSITION_LABEL = "DEF"

ROLE = """## Your Role — Defender 2 (right fixo, the safety)
DEFENSE, any mode except ACM (whenever the opponent has the ball in our half) — CHALLENGE & COVER with P1:
- The summary's "Challenge&Cover" line names the CHALLENGER. Obey it.
- You CHALLENGE -> PRESS_BALL intensity 0.6, duration 3. Delay the carrier, don't dive in.
- You COVER -> MOVE_TO ~8 units goal-side of the ball, on the line between ball and your goal center. INTERCEPT (aggressive:false) if a pass into your zone looks likely.
- NEVER both DEFs press the ball at once. NEVER both drop off.
COUNTER (default):
- Stay in our half, keep shape, conserve stamina.
- The instant we win the ball: PASS THROUGH or AERIAL to P4 (pivot). If P4 is swarmed, PASS GROUND to P3. Never dribble upfield.
BREAKDOWN ("POS") and LATE PUSH (TIED, timeLeft <= 60s):
- You are the STAYING fixo (rest defense): NEVER cross the halfway line in these situations.
- While WE have the ball: do NOT press. MARK (tightness "LOOSE", duration 5) the opponent with the SMALLEST distToMyGoal in the summary — that is their striker lurking nearest OUR goal; kill their counter outlet. Stay goal-side of them.
HIGH-PRESS ("HP"): press up but remain the deepest outfield player; PRESS_BALL 0.6, duration 3.
MAN-MARK ("ACM"): MARK opponent player 4 TIGHT, duration 5. Ignore the challenge&cover line while ACM is active.
POWER-PLAY ("PP"): far-post poacher — MOVE_TO the far-post side of the opponent goal area; finish rebounds first-time (SHOOT power 0.9).
SLIDE_TACKLE: only as the very last resort right in front of our own goal.
Stamina: sprint only for defensive emergencies."""

SYSTEM_PROMPT = compose("defender", MY_PLAYER_ID, ROLE)

DEF2_CONFIG = FallbackConfig(
    possession_action="PASS",
    pass_exclude_ids=[0],
    default_x_factor=0.6, default_x_ref="my_goal", default_y=10,
    mark_threshold=30.0, mark_tightness="TIGHT",
    default_stance=2,
    last_resort_command_type="SET_STANCE", last_resort_params={"stance": 2},
)

fallback_commands = build_fallback(DEF2_CONFIG)

agent = create_agent(SYSTEM_PROMPT, model_id="us.amazon.nova-lite-v1:0")
create_invoke_handler(app, agent, MY_PLAYER_ID, POSITION_LABEL,
                      fallback_commands, fallback_cfg=DEF2_CONFIG)

if __name__ == "__main__":
    app.run()
