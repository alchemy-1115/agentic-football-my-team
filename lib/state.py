"""Game state summarization utilities for AI soccer agents."""

import math

# Opponent GK further than this from their goal centre is flagged OPEN GOAL.
OPEN_GOAL_GK_DIST = 20.0


# ---------------------------------------------------------------------------
# Format-agnostic helpers — handle both new (agentId/teamCode/possessionAgentId)
# and old (playerId/teamId/possessionPlayerId) game server formats.
# ---------------------------------------------------------------------------

def _player_idx(p: dict) -> int:
    """Numeric index (0-4) from a player dict — new agentId or old playerId."""
    if "agentId" in p:
        try:
            return int(p["agentId"].rsplit("_", 1)[-1])
        except (ValueError, IndexError):
            return 0
    return p.get("playerId", 0)


def _is_my_team(p: dict, team_id: int) -> bool:
    """True if player belongs to team_id — new teamCode or old teamId."""
    if "teamCode" in p:
        return p["teamCode"] == ("home" if team_id == 0 else "away")
    return p.get("teamId") == team_id


def _possession_idx(ball: dict):
    """Numeric possession player index from ball dict — new possessionAgentId or old possessionPlayerId.
    Returns int or None."""
    agent_id = ball.get("possessionAgentId")
    if agent_id is not None:
        try:
            return int(agent_id.rsplit("_", 1)[-1])
        except (ValueError, IndexError):
            return None
    return ball.get("possessionPlayerId")


def get_goal_positions(team_id: int) -> tuple[float, float]:
    """Return (my_goal_x, opp_goal_x) based on team."""
    if team_id == 0:
        return -55.0, 55.0
    return 55.0, -55.0


def get_possession_info(ball: dict, players: list, team_id: int) -> tuple:
    """Return (possession_id, ball_status_str, we_have_ball)."""
    possession_id = _possession_idx(ball)
    if possession_id is not None:
        holder = next((p for p in players if _player_idx(p) == possession_id), None)
        if holder:
            is_mine = _is_my_team(holder, team_id)
            side = "MY" if is_mine else "OPP"
            return possession_id, f"{side} player {possession_id}", is_mine
        return possession_id, "unknown", False
    return None, "free", False


def dist(pos1: dict, pos2: dict) -> float:
    """Euclidean distance between two position dicts with x,y keys."""
    return math.sqrt(
        (pos1.get("x", 0) - pos2.get("x", 0)) ** 2
        + (pos1.get("y", 0) - pos2.get("y", 0)) ** 2
    )


def _score_status_line(game_time: float, score: dict, team_id: int) -> str:
    """Pre-computed match status so lightweight models never do the scoreboard math."""
    my_score = score.get("home", 0) if team_id == 0 else score.get("away", 0)
    opp_score = score.get("away", 0) if team_id == 0 else score.get("home", 0)
    if my_score > opp_score:
        status = f"WINNING by {my_score - opp_score}"
    elif my_score < opp_score:
        status = f"LOSING by {opp_score - my_score}"
    else:
        status = "TIED"
    time_left = max(0, 300 - game_time)
    return f"Match status: you are {status} | timeLeft={time_left:.0f}s"


def summarize_state(
    game_state: dict,
    team_id: int,
    my_player_id: int,
    position_label: str,
) -> str:
    """Build a concise text summary of the game state for a single-player agent."""
    ball = game_state.get("ball", {})
    ball_pos = ball.get("position", {"x": 0, "y": 0})
    score = game_state.get("score", {})
    game_time = game_state.get("gameTime", 0)
    play_mode = game_state.get("playMode", 0)
    players = game_state.get("players", [])

    my_team = sorted(
        [p for p in players if _is_my_team(p, team_id)],
        key=lambda p: _player_idx(p),
    )
    opponents = sorted(
        [p for p in players if not _is_my_team(p, team_id)],
        key=lambda p: _player_idx(p),
    )

    me = next((p for p in my_team if _player_idx(p) == my_player_id), None)
    possession_id, ball_status, _ = get_possession_info(ball, players, team_id)

    my_goal_x, opp_goal_x = get_goal_positions(team_id)

    lines = [
        _score_status_line(game_time, score, team_id),
        f"Time: {game_time:.0f}s | Score: {score.get('home',0)}-{score.get('away',0)} | "
        f"Team: {team_id} ({'HOME' if team_id == 0 else 'AWAY'}) | PlayMode: {play_mode}",
        f"Ball: ({ball_pos.get('x',0):.1f}, {ball_pos.get('y',0):.1f}) held by {ball_status}",
        f"Your goal at x={my_goal_x:.0f} | Opponent goal at x={opp_goal_x:.0f}",
        "",
    ]

    # My player info
    if me:
        pos = me.get("position", {})
        stam = me.get("stamina", 1.0)
        # The game server reports stamina as 0.0-1.0; show a percentage so
        # ":.0f" doesn't round every value to 0 or 1.
        stam_pct = stam * 100 if stam <= 1 else stam
        dist_ball = dist(pos, ball_pos)
        has_ball = possession_id == my_player_id
        extra = f" distOppGoal={abs(pos.get('x', 0) - opp_goal_x):.1f}" if position_label in ("MID", "FWD1", "FWD2") else ""
        lines.append(
            f">>> YOUR PLAYER ({position_label}, id={my_player_id}): "
            f"pos=({pos.get('x',0):.1f},{pos.get('y',0):.1f}) "
            f"stam={stam_pct:.0f}% distBall={dist_ball:.1f}{extra} hasBall={has_ball}"
        )
    lines.append("")

    # Teammates
    lines.append("Teammates:")
    for p in my_team:
        if _player_idx(p) == my_player_id:
            continue
        pos = p.get("position", {})
        pid = _player_idx(p)
        role = "GK" if pid == 0 else f"P{pid}"
        extra = ""
        if position_label == "MID":
            extra = f" distOppGoal={abs(pos.get('x', 0) - opp_goal_x):.1f}"
        lines.append(f"  {role}(id={pid}): ({pos.get('x',0):.1f},{pos.get('y',0):.1f}){extra}")

    lines.append("")

    # Opponents
    opp_header = "Opponents (defenders to watch):" if position_label in ("FWD1", "FWD2") else "Opponents:"
    lines.append(opp_header)
    for p in opponents:
        pos = p.get("position", {})
        pid = _player_idx(p)
        d_goal = abs(pos.get("x", 0) - my_goal_x)
        d_me = dist(pos, me.get("position", {})) if me else 0
        lines.append(f"  P{pid}: ({pos.get('x',0):.1f},{pos.get('y',0):.1f}) distToMyGoal={d_goal:.1f} distToMe={d_me:.1f}")

    # Opponent GK distance from THEIR goal, pre-computed for the FWD open-goal
    # rule — the list above only gives distToMyGoal, and light models can't be
    # trusted to do the subtraction.
    opp_gk = next((p for p in opponents if _player_idx(p) == 0), None)
    if opp_gk:
        gk_off = dist(opp_gk.get("position", {}), {"x": opp_goal_x, "y": 0})
        lines.append(
            f"Opponent GK: distFromTheirGoal={gk_off:.1f}"
            + (" (OFF THEIR LINE — OPEN GOAL)" if gk_off > OPEN_GOAL_GK_DIST else "")
        )

    # Parked-bus detector: how many opponents are inside OUR half right now.
    # Pre-computed so lightweight models can trigger BREAKDOWN without geometry.
    _opp_in_our_half = sum(
        1 for p in opponents
        if (p.get("position", {}).get("x", 0) < 0) == (my_goal_x < 0)
    )
    lines.append("")
    lines.append(
        f"Opponents in our half: {_opp_in_our_half}"
        + (" (they are PARKED DEEP — besiege them)" if _opp_in_our_half == 0 else "")
    )

    # Challenge & cover: pre-computed ball-distance comparison for the two DEFs
    # (players 1 and 2), so lightweight models don't have to do the geometry.
    # Only shown while we do NOT hold the ball — otherwise light models get
    # pulled into defensive commands during our own possession.
    _, _, _we_have_ball = get_possession_info(ball, players, team_id)
    if my_player_id in (1, 2) and not _we_have_ball:
        defs = {
            _player_idx(p): p.get("position", {})
            for p in my_team
            if _player_idx(p) in (1, 2)
        }
        if len(defs) == 2:
            d1 = dist(defs[1], ball_pos)
            d2 = dist(defs[2], ball_pos)
            challenger = 1 if d1 <= d2 else 2
            lines.append("")
            lines.append(
                f"Challenge&Cover: CHALLENGER is P{challenger} "
                f"(P1 distBall={d1:.1f}, P2 distBall={d2:.1f}). "
                f"{'You CHALLENGE.' if challenger == my_player_id else 'You COVER.'}"
            )

    # Coach instructions from teamChat (the sample library dropped these; our
    # codeword system depends on them reaching the LLM).
    team_chat = game_state.get("teamChat") or []
    if team_chat:
        lines.append("")
        lines.append("Coach instructions (newest last; codewords are absolute orders):")
        for msg in team_chat[-3:]:
            text = msg if isinstance(msg, str) else msg.get("message", str(msg)) if isinstance(msg, dict) else str(msg)
            lines.append(f"  Coach: {text}")

    return "\n".join(lines)
