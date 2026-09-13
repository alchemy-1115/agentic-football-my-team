"""Gateway Tool: calculate_pass_options

Rates every teammate as a pass target for the calling player, from the
interception risk of opponents near the pass lane and the pass distance.

Deployed as a Lambda behind AgentCore Gateway (see deploy-all.sh).
"""

from _game import ToolInputError, distance, error, read_match

LANE_WIDTH = 8.0  # opponents closer than this to the pass lane can intercept


def _interception_risk(passer: dict, receiver: dict, opponents: list[dict]) -> float:
    """Estimate interception probability (0-0.95) from opponents near the pass lane."""
    pass_dist = distance(passer, receiver)
    if pass_dist < 1:
        return 0.0

    dx = receiver["x"] - passer["x"]
    dy = receiver["y"] - passer["y"]
    risk = 0.0
    for opp in opponents:
        # Project the opponent onto the pass segment
        t = ((opp["x"] - passer["x"]) * dx + (opp["y"] - passer["y"]) * dy) / pass_dist ** 2
        t = max(0.0, min(1.0, t))
        closest = {"x": passer["x"] + t * dx, "y": passer["y"] + t * dy}
        lane_dist = distance(opp, closest)
        if lane_dist < LANE_WIDTH:
            risk = max(risk, 1.0 - lane_dist / LANE_WIDTH)
    return min(risk, 0.95)


def lambda_handler(event, context):
    try:
        match = read_match(event)
    except ToolInputError as e:
        return error(str(e))

    passer = match["me"]
    opponents = [o["position"] for o in match["opponents"]]

    options = []
    for tm in match["teammates"]:
        pos = tm["position"]
        dist = distance(passer, pos)
        risk = _interception_risk(passer, pos, opponents)
        success = max(0.05, 1.0 - risk - dist / 120.0)
        options.append({
            "player_id": tm["player_id"],
            "distance": round(dist, 1),
            "receiver_dist_to_opp_goal": round(distance(pos, match["opp_goal"]), 1),
            "interception_risk": round(risk, 2),
            "success_probability": round(success, 2),
            "recommended_type": "GROUND" if dist < 20 else ("THROUGH" if success > 0.5 else "AERIAL"),
        })

    options.sort(key=lambda o: o["success_probability"], reverse=True)
    return {"pass_options": options}
