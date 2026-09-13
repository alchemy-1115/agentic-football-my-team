"""Memory-backed drop-in replacement for agent_base.create_agent.

Same signature as agent_base.create_agent — including tools, so a Gateway
agent keeps its tactical tools — and an agent opts in by importing create_agent
from here instead. The Strands Agent is wrapped in an
AgentCoreMemorySessionManager, which keeps its conversation history across
game ticks (short-term memory).

Each match gets its own Memory session ("match-<team>-<position>-<start>"):
agent_base calls start_new_match() on the first tick of every match. A single
fixed session id replayed the previous match's history — including which way
we were attacking — into every new match. The Strands Agent is only built on
that first tick, so importing this module makes no AWS calls.

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
import time
import uuid

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


class MatchScopedMemoryAgent:
    """Callable like a Strands Agent, backed by a fresh AgentCore Memory session per match."""

    def __init__(self, system_prompt: str, model_id: str, tools: list | None,
                 memory_id: str, position: str, team_id: str) -> None:
        self._system_prompt = system_prompt + MEMORY_PROMPT
        self._model_id = model_id
        self._tools = tools
        self._memory_id = memory_id
        self._position = position
        self._team_id = team_id
        self.agent: Agent | None = None
        self.session_id: str | None = None

    def start_new_match(self) -> None:
        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
        from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

        # Drop the old match's agent first, so a failed rebuild can't fall back to it
        self.agent = None
        started = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        session_id = f"match-{self._team_id}-{self._position}-{started}-{uuid.uuid4().hex[:6]}"
        session_manager = AgentCoreMemorySessionManager(
            agentcore_memory_config=AgentCoreMemoryConfig(
                memory_id=self._memory_id,
                session_id=session_id,
                actor_id=f"{self._team_id}-{self._position}",
            ),
            region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
        )
        self.agent = Agent(
            model=BedrockModel(model_id=self._model_id),
            system_prompt=self._system_prompt,
            tools=self._tools,
            session_manager=session_manager,
        )
        self.session_id = session_id

    @property
    def messages(self) -> list:
        return self.agent.messages if self.agent else []

    def __call__(self, prompt, **kwargs):
        if self.agent is None:
            self.start_new_match()
        return self.agent(prompt, **kwargs)


def create_agent(
    system_prompt: str,
    model_id: str = "us.amazon.nova-micro-v1:0",
    tools: list | None = None,
) -> Agent | MatchScopedMemoryAgent:
    """Create an agent backed by AgentCore Memory (STM), one session per match."""
    memory_id = os.environ.get("MEMORY_ID")
    position = os.environ.get("AGENT_POSITION")
    team_id = os.environ.get("TEAM_ID", "my-team")

    if not memory_id or not position:
        missing = "MEMORY_ID" if not memory_id else "AGENT_POSITION"
        print(f"WARNING: {missing} not set — falling back to a stateless agent")
        return create_stateless_agent(system_prompt, model_id, tools)

    return MatchScopedMemoryAgent(system_prompt, model_id, tools, memory_id, position, team_id)
