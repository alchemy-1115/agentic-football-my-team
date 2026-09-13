"""Gateway Tool: find_open_space

Finds the reachable point in a zone (attack/midfield/defense) that is farthest
from every opponent, by sampling a grid over the zone.

Deployed as a Lambda behind AgentCore Gateway (see deploy-all.sh).
"""

from _game import ToolInputError, distance, error, read_match

GRID_STEP = 5
REACH_PENALTY = 0.15  # prefer space the player can actually get to

# Zone x-ranges for HOME (defends -x); mirrored for AWAY.
HOME_ZONES = {"defense": (-55, -15), "midfield": (-15, 15), "attack": (15, 52)}


def _nearest_opponent(point: dict, opponents: list[dict]) -> float:
    return min((distance(point, o) for o in opponents), default=999.0)


def lambda_handler(event, context):
    try:
        match = read_match(event)
    except ToolInputError as e:
        return error(str(e))

    zone = event.get("zone") or "attack"
    if zone not in HOME_ZONES:
        zone = "midfield"
    x_min, x_max = HOME_ZONES[zone]
    if match["team_id"] != 0:
        x_min, x_max = -x_max, -x_min

    me = match["me"]
    opponents = [o["position"] for o in match["opponents"]]

    best_point, best_score = None, float("-inf")
    for x in range(x_min, x_max + 1, GRID_STEP):
        for y in range(-30, 31, GRID_STEP):
            point = {"x": float(x), "y": float(y)}
            score = _nearest_opponent(point, opponents) - distance(point, me) * REACH_PENALTY
            if score > best_score:
                best_point, best_score = point, score

    return {
        "recommended_position": best_point,
        "nearest_opponent_distance": round(_nearest_opponent(best_point, opponents), 1),
        "distance_from_current": round(distance(best_point, me), 1),
        "zone": zone,
    }
