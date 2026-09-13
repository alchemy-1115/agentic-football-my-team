"""Gateway Tool: evaluate_shot

Estimates the calling player's chance of scoring from where they stand, from
distance, angle, the opponent goalkeeper's position and nearby blockers, and
recommends an aim point and power.

Deployed as a Lambda behind AgentCore Gateway (see deploy-all.sh).
"""

import math

from _game import ToolInputError, distance, error, read_match

GOAL_HALF_WIDTH = 5.0  # approximate half-width of the goal
SHOOT_THRESHOLD = 0.25


def lambda_handler(event, context):
    try:
        match = read_match(event)
    except ToolInputError as e:
        return error(str(e))

    shooter = match["me"]
    goal = match["opp_goal"]
    gk = next((o["position"] for o in match["opponents"] if o["player_id"] == 0), goal)
    blockers = [o["position"] for o in match["opponents"] if o["player_id"] != 0]

    dist_to_goal = distance(shooter, goal)

    # Angle factor: wider angle = better chance
    angle_factor = min(1.0, math.atan2(GOAL_HALF_WIDTH, dist_to_goal) / 0.15)
    # Distance factor: closer = better
    distance_factor = max(0.0, 1.0 - dist_to_goal / 55.0)
    # GK off-centre or off their line = better chance
    gk_factor = min(1.0, abs(gk["y"] - goal["y"]) / 8.0) * 0.3
    gk_dist_factor = min(1.0, distance(gk, goal) / 15.0) * 0.2

    blocker_penalty = 0.0
    for b in blockers:
        b_dist = distance(shooter, b)
        if b_dist < 10:
            blocker_penalty += (10 - b_dist) / 10.0 * 0.15
    blocker_penalty = min(blocker_penalty, 0.4)

    probability = max(0.02, min(0.95,
        distance_factor * 0.45 + angle_factor * 0.25 + gk_factor + gk_dist_factor - blocker_penalty
    ))

    # Aim away from the side the GK is leaning to
    if gk["y"] > 1:
        aim = "BL" if shooter["y"] > 0 else "BR"
    elif gk["y"] < -1:
        aim = "TL" if shooter["y"] > 0 else "TR"
    else:
        aim = "TR" if shooter["y"] <= 0 else "TL"

    return {
        "success_probability": round(probability, 2),
        "distance_to_goal": round(dist_to_goal, 1),
        "goalkeeper_dist_from_goal": round(distance(gk, goal), 1),
        "recommended_aim": aim,
        "recommended_power": round(min(1.0, 0.6 + dist_to_goal / 80.0), 2),
        "should_shoot": probability > SHOOT_THRESHOLD,
    }
