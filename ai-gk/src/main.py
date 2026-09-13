"""my-team GK — Player 0. Nova Micro. Goal-line keeper, counter trigger, PP 5th passer."""

import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))
from _bootstrap import setup_lib_path; setup_lib_path(__file__)

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from memory_agent_base import create_agent
from agent_base import create_invoke_handler
from fallback import build_fallback, GK_CONFIG
from prompt_parts import compose

app = BedrockAgentCoreApp()

MY_PLAYER_ID = 0
POSITION_LABEL = "GK"

ROLE = """## Your Role — Goalkeeper
All modes EXCEPT POWER-PLAY:
- Stay within ~8 units of your goal line, always between the ball and the goal center
- INTERCEPT (aggressive:false) when the ball is loose within ~10 units of you
- With the ball, distribute fast — this starts our counter:
  1. GK_DISTRIBUTE KICK to P4 (our pivot up front) whenever they are not fully surrounded
  2. otherwise GK_DISTRIBUTE THROW to P1 or P2 (whichever is freer)
- If opponents press our build-up hard, never dribble — escape with the long KICK to P4
- NEVER sprint (save stamina for saves). NEVER leave the goal empty.
POWER-PLAY ("PP") only:
- Join the attack as the 5th outfield passer: MOVE_TO (target_x, 0) where target_x has the SAME SIGN as the opponent goal x and magnitude 10. Opponent goal at x=55 -> target_x=10 (NOT -10). Opponent goal at x=-55 -> target_x=-10.
- Keep the ball moving: PASS GROUND to the free teammate; SHOOT only from < 20 units with a clear lane
- The instant we lose the ball, MOVE_TO your goal with sprint true"""

SYSTEM_PROMPT = compose("goalkeeper", MY_PLAYER_ID, ROLE)

fallback_commands = build_fallback(GK_CONFIG)

agent = create_agent(SYSTEM_PROMPT, model_id="us.amazon.nova-micro-v1:0")
create_invoke_handler(app, agent, MY_PLAYER_ID, POSITION_LABEL,
                      fallback_commands, fallback_cfg=GK_CONFIG)

if __name__ == "__main__":
    app.run()
