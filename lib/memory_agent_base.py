"""Memory-backed drop-in replacement for agent_base.create_agent.

Same signature as agent_base.create_agent — including tools, so a Gateway
agent keeps its tactical tools — and an agent opts in by importing create_agent
from here instead. The Strands Agent is wrapped in an
AgentCoreMemorySessionManager, which keeps its conversation history across
game ticks (short-term memory).

Env vars, all injected by deploy-all.sh:
  MEMORY_ID       — AgentCore Memory resource ID (created by create_memory.py)
  AGENT_POSITION  — this runtime's agent name, e.g. "ai-gk"; keeps each
                    position's history separate
  TEAM_ID         — actor_id / session_id prefix (default: "my-team")

Missing MEMORY_ID or AGENT_POSITION falls back to the plain stateless agent
rather than failing to start: a forgetful keeper beats no keeper at all, and
a shared AGENT_POSITION would interleave all five agents into one history.
"""

import os
from strands import Agent
from strands.models import BedrockModel

from agent_base import create_agent as create_stateless_agent

MEMORY_PROMPT = """

## Memory — you remember earlier ticks of this match
Your conversation history carries what you already saw and did. Use it to:
- Recognise opponent patterns (who shoots, who overlaps, which side they attack)
- Stay consistent: do not flip-flop between commands tick after tick
- Remember the newest coach codeword even when it scrolled out of the summary
- Recall what already failed this match and stop repeating it"""


def create_agent(
    system_prompt: str,
    model_id: str = "us.amazon.nova-micro-v1:0",
    tools: list | None = None,
) -> Agent:
    """Create a Strands Agent backed by AgentCore Memory (STM)."""
    memory_id = os.environ.get("MEMORY_ID")
    position = os.environ.get("AGENT_POSITION")
    team_id = os.environ.get("TEAM_ID", "my-team")

    if not memory_id or not position:
        missing = "MEMORY_ID" if not memory_id else "AGENT_POSITION"
        print(f"WARNING: {missing} not set — falling back to a stateless agent")
        return create_stateless_agent(system_prompt, model_id, tools)

    from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
    from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

    session_manager = AgentCoreMemorySessionManager(
        agentcore_memory_config=AgentCoreMemoryConfig(
            memory_id=memory_id,
            session_id=f"match-{team_id}-{position}",
            actor_id=f"{team_id}-{position}",
        ),
        region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
    )

    return Agent(
        model=BedrockModel(model_id=model_id),
        system_prompt=system_prompt + MEMORY_PROMPT,
        tools=tools,
        session_manager=session_manager,
    )
