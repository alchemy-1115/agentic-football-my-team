"""Base agent factory for AI soccer position agents."""

import json
from typing import Callable
from strands import Agent
from strands.models import BedrockModel

from parsing import parse_commands
from state import resolve_goal_positions, summarize_state
from fallback import FallbackConfig, build_last_resort

# gameTime jumping back by more than this means a new match has started
NEW_MATCH_REWIND_SECONDS = 5.0
# Cap for the once-per-match payload dump (CloudWatch log events are size-limited)
PAYLOAD_LOG_LIMIT = 8000


def create_agent(
    system_prompt: str,
    model_id: str = "us.amazon.nova-micro-v1:0",
    tools: list | None = None,
) -> Agent:
    """Create a Strands Agent with the given system prompt (and optional tools)."""
    model = BedrockModel(model_id=model_id)
    return Agent(model=model, system_prompt=system_prompt, tools=tools)


def resolve_team_id(prompt_data: dict) -> tuple[int, str]:
    """(team_id, source) — teamId as an int or numeric string, else a "home"/"away" teamCode.

    Falls back to 0 (HOME) with source "default". The invoke handler warns about
    that: a wrong team makes every player read the other team's positions.
    """
    raw = prompt_data.get("teamId")
    if raw is not None:
        try:
            return int(raw), "teamId"
        except (TypeError, ValueError):
            pass
    code = str(prompt_data.get("teamCode") or "").lower()
    if code in ("home", "away"):
        return (0 if code == "home" else 1), "teamCode"
    return 0, "default"


def describe_payload(payload, prompt_data: dict, context) -> str:
    """Compact JSON of what the game server actually sent, for the match-start log.

    The agents' assumptions about the payload (team fields, player ids, which
    end each team defends, playMode values) are otherwise unverified; this puts
    the real shape in CloudWatch once per match instead of on every tick.
    """
    game_state = prompt_data.get("gameState") or {}
    players = game_state.get("players") or []
    shape = {
        "session_id": getattr(context, "session_id", None),
        "payload_keys": sorted(payload) if isinstance(payload, dict) else type(payload).__name__,
        "prompt_fields": {k: v for k, v in prompt_data.items() if k != "gameState"},
        "teamId_present": "teamId" in prompt_data,
        "gameState_keys": sorted(game_state),
        "playMode": game_state.get("playMode"),
        "gameTime": game_state.get("gameTime"),
        "score": game_state.get("score"),
        "ball": game_state.get("ball"),
        "player_keys": sorted(players[0]) if players else [],
        "players": [
            {k: p.get(k) for k in ("agentId", "playerId", "teamCode", "teamId", "position") if k in p}
            for p in players
        ],
    }
    return json.dumps(shape, default=str)[:PAYLOAD_LOG_LIMIT]


class MatchTracker:
    """Spots the first tick of each match: the runtime's first tick, or gameTime jumping backwards."""

    def __init__(self) -> None:
        self.last_game_time: float | None = None
        self.match_count = 0

    def is_new_match(self, game_time: float) -> bool:
        new = self.last_game_time is None or game_time + NEW_MATCH_REWIND_SECONDS < self.last_game_time
        self.last_game_time = game_time
        if new:
            self.match_count += 1
        return new


def start_new_match(agent) -> None:
    """Forget the previous match so its history can't steer this one."""
    if hasattr(agent, "start_new_match"):
        agent.start_new_match()  # memory-backed agent: fresh AgentCore Memory session
    else:
        agent.messages.clear()  # plain Strands Agent: in-process history only


def create_invoke_handler(
    app,
    agent: Agent,
    my_player_id: int,
    position_label: str,
    fallback_fn: Callable[[dict, int, int], list[dict]],
    fallback_cfg: FallbackConfig,
    on_tick: Callable[[dict, int, int], None] | None = None,
):
    """Create and register the @app.entrypoint invoke handler.

    Three layers of error handling, from best to worst:
      1. LLM response → parse into commands
      2. fallback_fn(game_state, team_id, my_player_id) → rule-based commands
      3. last-resort command from fallback_cfg → single safe command

    On the first tick of every match the agent's conversation is reset and the
    payload shape is logged once.

    on_tick(game_state, team_id, my_player_id), if given, runs before the LLM
    call — Gateway agents use it to hand the raw game state to their tools.
    """
    log = app.logger
    last_resort = build_last_resort(fallback_cfg, my_player_id)
    tracker = MatchTracker()

    @app.entrypoint
    async def invoke(payload, context):
        try:
            prompt = payload.get("prompt", "{}")
            prompt_data = json.loads(prompt) if isinstance(prompt, str) else prompt

            game_state = prompt_data.get("gameState", {})
            team_id, team_source = resolve_team_id(prompt_data)

            # Honor myPlayers from payload if present, otherwise use configured player ID
            my_players = prompt_data.get("myPlayers", [my_player_id])
            effective_pid = my_players[0] if my_players else my_player_id

            try:
                game_time = float(game_state.get("gameTime") or 0)
            except (TypeError, ValueError):
                game_time = 0.0
            if tracker.is_new_match(game_time):
                start_new_match(agent)
                try:
                    shape = describe_payload(payload, prompt_data, context)
                except Exception as e:
                    shape = f"<unavailable: {e}>"
                log.info(f"{position_label} match #{tracker.match_count} started (gameTime={game_time:.0f}), "
                         f"conversation reset. Payload shape: {shape}")
                if team_source == "default":
                    log.warn(f"{position_label} payload has no teamId — assuming team 0 (HOME). "
                             "If our players attack their own goal, check the payload shape above.")

            state_summary = summarize_state(
                game_state, team_id, effective_pid, position_label
            )
            my_goal_x, _ = resolve_goal_positions(game_state.get("players") or [], team_id)
            log.info(f"{position_label} agent invoked for team {team_id} ({team_source}), "
                     f"controlling player {effective_pid}, our goal x={my_goal_x:.0f}")

            if on_tick:
                on_tick(game_state, team_id, effective_pid)
            response = agent(state_summary)
            response_text = str(response)

            def on_recovered(raw: str) -> None:
                # The model wrote Python-flavoured JSON (usually `True`/`False`/`None`).
                # We recovered it rather than dropping the command and falling back —
                # logged so you can see how often your model does this.
                log.warn(f"{position_label} recovered malformed JSON from the model: {raw[:200]}")

            commands = parse_commands(response_text, team_id, effective_pid, on_recovered)

            if commands:
                log.info(f"LLM returned {len(commands)} commands: "
                         f"{[c.get('commandType') for c in commands]}")
                yield json.dumps(commands)
            else:
                log.warn(f"LLM parse failed, using fallback. Response: {response_text[:200]}")
                commands = fallback_fn(game_state, team_id, effective_pid)
                log.info(f"Fallback returned {len(commands)} commands")
                yield json.dumps(commands)

        except Exception as e:
            log.error(f"{position_label} agent error: {e}")
            try:
                prompt_data = json.loads(payload.get("prompt", "{}"))
                team_id, _ = resolve_team_id(prompt_data)
                my_players = prompt_data.get("myPlayers", [my_player_id])
                effective_pid = my_players[0] if my_players else my_player_id
                commands = fallback_fn(
                    prompt_data.get("gameState", {}),
                    team_id,
                    effective_pid,
                )
                yield json.dumps(commands)
            except Exception:
                cmd = dict(last_resort)
                cmd["teamId"] = 0  # best guess when payload parsing also failed
                yield json.dumps([cmd])

    return invoke
