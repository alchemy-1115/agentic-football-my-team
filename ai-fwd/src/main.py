"""my-team FWD — Player 4. Nova Micro. Pivot striker, open-goal punisher, counter out-ball."""

import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))
from _bootstrap import setup_lib_path; setup_lib_path(__file__)

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from agent_base import create_agent, create_invoke_handler
from fallback import build_fallback, FallbackConfig
from prompt_parts import compose

app = BedrockAgentCoreApp()

MY_PLAYER_ID = 4
POSITION_LABEL = "FWD1"

ROLE = """## Your Role — Forward (pivot / target striker)
ALWAYS, in every mode:
- You are the highest player. There is NO offside — camp on the shoulder of their last defender.
- OPEN-GOAL RULE (top priority when you have the ball): if the summary's "Opponent GK" line says OPEN GOAL, SHOOT immediately — aim_location "CENTER", power 1.0, from anywhere on the pitch.
- Otherwise SHOOT from < 25 units: far corner, power 0.85.
COUNTER (default):
- You are the out-ball. When a teammate wins possession, sprint into the space behind their defense to receive the THROUGH/AERIAL pass — then finish.
- Pivot play: receiving with your back to goal under tight marking, lay off PASS GROUND to P3 arriving, then spin toward goal for the return THROUGH.
- Defensive work: press only down to the halfway line, never deeper — save your stamina for the next break.
BREAKDOWN ("POS"): pivot on their last line — drag defenders, lay off to P3 or P1, then attack the far post (segundo palo) when P3 shoots.
HIGH-PRESS ("HP"): first line of the press — PRESS_BALL 0.8, duration 3, on their carrier or GK.
MAN-MARK ("ACM"): MARK opponent player 1 TIGHT, duration 5.
POWER-PLAY ("PP"): near-post poacher at the OPPONENT goal line. MOVE_TO (target_x, 4) where target_x has the SAME SIGN as the opponent goal x in the summary and magnitude 50. SHOOT any loose ball first time (power 0.9).
LATE PUSH (TIED, timeLeft <= 60s): stay high and demand the ball — we are going for the win."""

SYSTEM_PROMPT = compose("forward", MY_PLAYER_ID, ROLE)

FWD_CONFIG = FallbackConfig(
    possession_action="SHOOT_OR_ADVANCE",
    advance_x_factor=0.6, advance_y=0, advance_sprint=True,
    support_x_factor=0.5, support_y=0, support_sprint=True,
    default_x_factor=0.4, default_x_ref="opp_goal", default_y=0,
    press_distance=20.0, press_intensity=0.7,
    shoot_aim="TR", shoot_power=0.9,
    default_stance=1,
    last_resort_command_type="PRESS_BALL", last_resort_params={"intensity": 0.6},
    last_resort_duration=3,
)

fallback_commands = build_fallback(FWD_CONFIG)

agent = create_agent(SYSTEM_PROMPT, model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")
create_invoke_handler(app, agent, MY_PLAYER_ID, POSITION_LABEL,
                      fallback_commands, fallback_cfg=FWD_CONFIG)

if __name__ == "__main__":
    app.run()
