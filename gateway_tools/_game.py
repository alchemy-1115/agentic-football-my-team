"""Game-state helpers shared by the tactical-tool Lambdas.

A Lambda can't import the agents' lib/, so this mirrors the parts of
lib/state.py the tools need; deploy-all.sh zips this file next to each tool.
Handles both the new (agentId/teamCode/possessionAgentId) and old
(playerId/teamId/possessionPlayerId) game server formats.
"""

import math


class ToolInputError(ValueError):
    """The tool arguments don't describe a usable match state."""


def player_idx(p: dict) -> int:
    """Numeric index (0-4) from a player dict — new agentId or old playerId."""
    if "agentId" in p:
        try:
            return int(str(p["agentId"]).rsplit("_", 1)[-1])
        except ValueError:
            return 0
    return int(p.get("playerId", 0))


def is_team(p: dict, team_id: int) -> bool:
    """True if player belongs to team_id — new teamCode or old teamId."""
    if "teamCode" in p:
        return p["teamCode"] == ("home" if team_id == 0 else "away")
    return p.get("teamId") == team_id


def possession(ball: dict, players: list, team_id: int) -> tuple[int | None, bool]:
    """(index of the player holding the ball, True if they are on team_id)."""
    agent_id = ball.get("possessionAgentId")
    if agent_id is not None:
        try:
            idx = int(str(agent_id).rsplit("_", 1)[-1])
        except ValueError:
            return None, False
    else:
        idx = ball.get("possessionPlayerId")
    if idx is None:
        return None, False
    # Same lookup as lib/state.py: agentId carries no team, so the first
    # player with that index wins.
    holder = next((p for p in players if player_idx(p) == idx), None)
    return idx, holder is not None and is_team(holder, team_id)


def position(p: dict) -> dict:
    pos = p.get("position") or {}
    return {"x": float(pos.get("x", 0)), "y": float(pos.get("y", 0))}


def distance(a: dict, b: dict) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


# Goalkeepers closer together than this can't tell us which end is whose.
SIDE_GK_MIN_GAP = 20.0


def goals(players: list, team_id: int) -> tuple[dict, dict]:
    """(my goal centre, opponent goal centre), read from where the two GKs stand.

    Mirrors lib/state.resolve_goal_positions: the GK further left defends the
    left goal; falls back to HOME (team 0) defends -x when that can't be told.
    """
    left, right = {"x": -55.0, "y": 0.0}, {"x": 55.0, "y": 0.0}
    my_gk = next((p for p in players if is_team(p, team_id) and player_idx(p) == 0), None)
    opp_gk = next((p for p in players if not is_team(p, team_id) and player_idx(p) == 0), None)
    if my_gk and opp_gk:
        my_x, opp_x = position(my_gk)["x"], position(opp_gk)["x"]
        if abs(my_x - opp_x) >= SIDE_GK_MIN_GAP:
            return (left, right) if my_x < opp_x else (right, left)
    return (left, right) if team_id == 0 else (right, left)


def read_match(event: dict) -> dict:
    """Turn the Gateway tool arguments into positions for the calling player.

    Gateway passes the tool arguments as the Lambda event itself (a map of the
    inputSchema properties); the agent attaches game_state/team_id/player_id.
    """
    try:
        game_state = event["game_state"]
        team_id = int(event["team_id"])
        player_id = int(event["player_id"])
    except (KeyError, TypeError, ValueError) as e:
        raise ToolInputError(f"missing or invalid match context: {e!r}") from e
    if not isinstance(game_state, dict):
        raise ToolInputError("game_state must be an object")

    players = game_state.get("players") or []
    mine = [p for p in players if is_team(p, team_id)]
    me = next((p for p in mine if player_idx(p) == player_id), None)
    if me is None:
        raise ToolInputError(f"player {player_id} of team {team_id} not found in game_state")

    ball = game_state.get("ball") or {}
    holder_idx, holder_is_mine = possession(ball, players, team_id)
    my_goal, opp_goal = goals(players, team_id)
    return {
        "team_id": team_id,
        "player_id": player_id,
        "me": position(me),
        "teammates": [
            {"player_id": player_idx(p), "position": position(p)}
            for p in mine if player_idx(p) != player_id
        ],
        "opponents": [
            {"player_id": player_idx(p), "position": position(p)}
            for p in players if not is_team(p, team_id)
        ],
        "ball": position(ball),
        "holder_idx": holder_idx,
        "holder_is_mine": holder_is_mine,
        "my_goal": my_goal,
        "opp_goal": opp_goal,
    }


def error(message: str) -> dict:
    return {"error": message}
