"""my-team MID — Player 3. Nova Pro (the brain). Link play, width, parallela, segundo palo."""

import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))
from _bootstrap import setup_lib_path; setup_lib_path(__file__)

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from agent_base import create_agent, create_invoke_handler
from fallback import build_fallback, MID_CONFIG
from prompt_parts import compose

app = BedrockAgentCoreApp()

MY_PLAYER_ID = 3
POSITION_LABEL = "MID"

ROLE = """## Your Role — Midfielder (the ala / link between defense and attack)
COUNTER (default):
- Hold central midfield; always offer a passing lane to whoever has the ball.
- The instant we win the ball, play forward FAST: first look THROUGH to P4 into space; if blocked, carry forward (MOVE_TO, sprint true) a few units and then release.
- When the opponent attacks, track back to the edge of our defensive third — second screen in front of the DEFs.
BREAKDOWN ("POS") and LATE PUSH (TIED, timeLeft <= 60s):
- Provide WIDTH: position near a touchline (|y| >= 18) in the opponent half to stretch their block.
- Parallela: when a teammate faces forward with the ball on your side, sprint down the touchline past their defender — the THROUGH pass comes into your run.
- Pivot lay-off: when P4 holds the ball up with their back to goal, arrive at ~20 units from their goal for the lay-off and SHOOT (power 0.75, far corner).
- Segundo palo: when a TEAMMATE (not you) has the ball in the attacking third about to shoot, MOVE_TO the far-post side of their goal area for the rebound.
HIGH-PRESS ("HP"): second wave of the press — PRESS_BALL 0.7, duration 3; position to cut their easiest escape pass first.
MAN-MARK ("ACM"): MARK opponent player 2 TIGHT, duration 5. Follow them everywhere.
POWER-PLAY ("PP"): central distributor just outside their box; one- and two-touch passes to the free man; SHOOT the moment a lane opens.
SHOOT from < 25 units with a clear sight (far corner, power 0.8).
Stamina: you cover the most ground — move with sprint:false whenever the ball is safe."""

SYSTEM_PROMPT = compose("midfielder", MY_PLAYER_ID, ROLE)

fallback_commands = build_fallback(MID_CONFIG)

agent = create_agent(SYSTEM_PROMPT, model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")
create_invoke_handler(app, agent, MY_PLAYER_ID, POSITION_LABEL,
                      fallback_commands, fallback_cfg=MID_CONFIG)

if __name__ == "__main__":
    app.run()
