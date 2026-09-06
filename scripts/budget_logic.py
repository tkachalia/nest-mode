"""
Turns usage data into a monthly cost projection, and decides whether the
thermostat setpoint should move, within the comfort limits the user set.

Design rule that overrides everything else: comfort limits always win.
This will never push past cooling_ceiling_f or heating_floor_f, even if
that means going over budget. It will tell you instead of forcing you
to be uncomfortable.
"""

from calendar import monthrange
from datetime import date


def project_month_end_cost(state: dict, config: dict, today: date) -> dict:
    days_in_month = monthrange(today.year, today.month)[1]
    day_of_month = today.day

    spend_so_far = state.get("month_spend_dollars", 0.0)
    avg_daily_spend = spend_so_far / max(day_of_month - 1, 1)
    projected_total = avg_daily_spend * days_in_month

    budget = config["budget"]["monthly_target_dollars"]
    return {
        "spend_so_far": round(spend_so_far, 2),
        "avg_daily_spend": round(avg_daily_spend, 2),
        "projected_total": round(projected_total, 2),
        "budget": budget,
        "over_under": round(projected_total - budget, 2),
        "pct_of_budget": round((projected_total / budget) * 100) if budget else None,
    }


def decide_adjustment_for_zone(
    projection: dict, thermostat: dict, config: dict
) -> dict:
    """
    Returns a dict describing what action to take, one of:
      - "hold": no change needed, or already at comfort limit
      - "nudge_cool": raise cooling setpoint by one step
      - "nudge_heat": lower heating setpoint by one step
    """
    step = config["budget"]["step_size_f"]
    ceiling = config["comfort"]["cooling_ceiling_f"]
    floor = config["comfort"]["heating_floor_f"]
    over_budget = projection["over_under"] > 0

    mode = thermostat["mode"]

    if not over_budget:
        return {"action": "hold", "reason": "tracking within budget"}

    if mode == "COOL":
        current = thermostat["cool_setpoint_f"]
        if current is None:
            return {"action": "hold", "reason": "no cool setpoint reported"}
        if current >= ceiling:
            return {
                "action": "hold",
                "reason": (
                    f"at comfort ceiling ({ceiling}F), over budget by "
                    f"${projection['over_under']}, likely a hardware/"
                    f"insulation issue rather than a thermostat fix"
                ),
            }
        return {
            "action": "nudge_cool",
            "new_setpoint_f": min(current + step, ceiling),
            "reason": f"over budget by ${projection['over_under']}",
        }

    if mode == "HEAT":
        current = thermostat["heat_setpoint_f"]
        if current is None:
            return {"action": "hold", "reason": "no heat setpoint reported"}
        if current <= floor:
            return {
                "action": "hold",
                "reason": (
                    f"at comfort floor ({floor}F), over budget by "
                    f"${projection['over_under']}, likely a hardware/"
                    f"insulation issue rather than a thermostat fix"
                ),
            }
        return {
            "action": "nudge_heat",
            "new_setpoint_f": max(current - step, floor),
            "reason": f"over budget by ${projection['over_under']}",
        }

    return {"action": "hold", "reason": f"thermostat mode is {mode}, nothing to adjust"}
