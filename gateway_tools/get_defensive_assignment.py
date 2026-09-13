"""Gateway Tool: get_defensive_assignment

Ranks opponents by threat (closeness to our goal, closeness to the ball, and
holding the ball) and recommends who the calling player should mark.

Deployed as a Lambda behind AgentCore Gateway (see deploy-all.sh).
"""

from _game import ToolInputError, distance, error, read_match

MARK_RANGE = 30.0  # beyond this the player can't reach their man — intercept instead


def lambda_handler(event, context):
    try:
        match = read_match(event)
    except ToolInputError as e:
        return error(str(e))

    threats = []
    for opp in match["opponents"]:
        pos = opp["position"]
        dist_to_goal = distance(pos, match["my_goal"])
        dist_to_ball = distance(pos, match["ball"])
        # An opponent only holds the ball if the holder is NOT on our team —
        # agentId indices repeat across teams.
        has_ball = not match["holder_is_mine"] and opp["player_id"] == match["holder_idx"]

        goal_threat = max(0.0, 1.0 - dist_to_goal / 80.0)
        ball_bonus = 0.3 if dist_to_ball < 10 else (0.15 if dist_to_ball < 20 else 0.0)
        possession_bonus = 0.4 if has_ball else 0.0
        threat_score = goal_threat + ball_bonus + possession_bonus

        threats.append({
            "player_id": opp["player_id"],
            "threat_score": round(threat_score, 2),
            "distance_to_goal": round(dist_to_goal, 1),
            "distance_to_me": round(distance(pos, match["me"]), 1),
            "has_ball": has_ball,
            "recommended_tightness": "TIGHT" if threat_score > 0.7 else "LOOSE",
        })

    threats.sort(key=lambda t: t["threat_score"], reverse=True)
    top = threats[0] if threats else None
    return {
        "threats": threats,
        "recommended_mark": top,
        "action": "MARK" if top and top["distance_to_me"] < MARK_RANGE else "INTERCEPT",
    }
