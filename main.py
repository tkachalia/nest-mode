"""
Runs on a schedule (every 15 min via GitHub Actions). Each run:
  1. Once a day, pulls yesterday's actual Duke Energy usage and reconciles
     the month-to-date spend total against it.
  2. Reads current status for EACH Nest zone and adds estimated cost for
     HVAC runtime since the last run, summed across zones.
  3. Projects month-end cost and decides, per zone, whether to nudge that
     zone's setpoint, never past the comfort limits.
  4. Writes state.json (committed back to the repo by the workflow) so
     the dashboard can read it, and sends a notification if anything
     changed or a limit was hit.

Budget and comfort limits can be overridden by dashboard/settings.json,
which the phone dashboard writes to directly via the GitHub API. This
keeps those editable without touching the secret-holding config.json.
"""

import json
import logging
from datetime import datetime, date, timezone
from pathlib import Path

import duke_energy
import nest_control
import budget_logic
import notify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "dashboard" / "state.json"
SETTINGS_PATH = ROOT / "dashboard" / "settings.json"
HISTORY_PATH = ROOT / "dashboard" / "history.json"


def load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def effective_config(config: dict) -> dict:
    """
    Merges dashboard/settings.json (user-editable via phone) over the
    defaults in config.json (only editable by re-pushing the secret).
    """
    settings = load_json(SETTINGS_PATH)
    merged = json.loads(json.dumps(config))  # deep copy
    if "monthly_target_dollars" in settings:
        merged["budget"]["monthly_target_dollars"] = settings["monthly_target_dollars"]
    if "cooling_ceiling_f" in settings:
        merged["comfort"]["cooling_ceiling_f"] = settings["cooling_ceiling_f"]
    if "heating_floor_f" in settings:
        merged["comfort"]["heating_floor_f"] = settings["heating_floor_f"]
    if "step_size_f" in settings:
        merged["budget"]["step_size_f"] = settings["step_size_f"]
    return merged


def estimate_runtime_cost(thermostat, zone_cfg, rate_per_kwh, minutes_elapsed):
    if thermostat["hvac_status"] == "COOLING":
        watts = zone_cfg["estimated_watts_cooling"]
    elif thermostat["hvac_status"] == "HEATING":
        watts = zone_cfg["estimated_watts_heating"]
    else:
        return 0.0
    kwh = (watts / 1000) * (minutes_elapsed / 60)
    return kwh * rate_per_kwh


def load_history() -> list:
    if HISTORY_PATH.exists():
        return json.loads(HISTORY_PATH.read_text())
    return []


def save_history(history: list) -> None:
    HISTORY_PATH.write_text(json.dumps(history, indent=2))


def update_day_accumulator(state: dict, zone_statuses: dict, today_iso: str) -> None:
    """
    Tracks running min/max/avg per zone for the current day, so we can
    finalize a real min-max range (not just an end-of-day snapshot) when
    the day rolls over.
    """
    acc = state.get("day_accumulator")
    if not acc or acc.get("date") != today_iso:
        acc = {"date": today_iso, "spend_at_start": state.get("month_spend_dollars", 0.0), "zones": {}}

    for name, t in zone_statuses.items():
        z = acc["zones"].setdefault(
            name,
            {
                "temp_min": None, "temp_max": None, "temp_sum": 0.0,
                "humidity_min": None, "humidity_max": None, "humidity_sum": 0.0,
                "count": 0,
            },
        )
        temp = t.get("ambient_f")
        humidity = t.get("ambient_humidity_percent")
        if temp is not None:
            z["temp_min"] = temp if z["temp_min"] is None else min(z["temp_min"], temp)
            z["temp_max"] = temp if z["temp_max"] is None else max(z["temp_max"], temp)
            z["temp_sum"] += temp
        if humidity is not None:
            z["humidity_min"] = humidity if z["humidity_min"] is None else min(z["humidity_min"], humidity)
            z["humidity_max"] = humidity if z["humidity_max"] is None else max(z["humidity_max"], humidity)
            z["humidity_sum"] += humidity
        z["count"] += 1

    state["day_accumulator"] = acc


def finalize_day_if_rolled_over(state: dict, today_iso: str) -> None:
    """
    If the accumulator belongs to a previous day, close it out into
    history.json and reset for today.
    """
    acc = state.get("day_accumulator")
    if not acc or acc["date"] == today_iso:
        return

    history = load_history()
    day_spend = round(state.get("month_spend_dollars", 0.0) - acc["spend_at_start"], 2)

    zones_summary = {}
    for name, z in acc["zones"].items():
        zones_summary[name] = {
            "temp_min": z["temp_min"],
            "temp_max": z["temp_max"],
            "temp_avg": round(z["temp_sum"] / z["count"], 1) if z["count"] else None,
            "humidity_min": z["humidity_min"],
            "humidity_max": z["humidity_max"],
            "humidity_avg": round(z["humidity_sum"] / z["count"], 1) if z["count"] else None,
        }

    history.append(
        {
            "type": "daily",
            "date": acc["date"],
            "spend": day_spend,
            "zones": zones_summary,
        }
    )
    # Keep a rolling ~13 months of daily detail so the file doesn't grow forever.
    daily_entries = [e for e in history if e.get("type") == "daily"]
    other_entries = [e for e in history if e.get("type") != "daily"]
    daily_entries = daily_entries[-400:]
    save_history(other_entries + daily_entries)

    state["day_accumulator"] = None



    raw_config = load_json(CONFIG_PATH)
    config = effective_config(raw_config)
    state = load_json(STATE_PATH)
    now = datetime.now(timezone.utc)
    today = date.today()

    if state.get("current_month") != today.month:
        state = {
            "current_month": today.month,
            "month_spend_dollars": 0.0,
            "last_run_iso": None,
        }

    if state.get("last_duke_pull_date") != today.isoformat():
        kwh_yesterday = duke_energy.get_yesterdays_kwh_sync(config, state)
        if kwh_yesterday is not None:
            actual_cost_yesterday = kwh_yesterday * config["duke_energy"]["rate_per_kwh"]
            state["month_spend_dollars"] = state.get(
                "reconciled_spend_before_today", state["month_spend_dollars"]
            ) + actual_cost_yesterday
            state["reconciled_spend_before_today"] = state["month_spend_dollars"]
        state["last_duke_pull_date"] = today.isoformat()

    minutes_elapsed = config["budget"]["check_interval_minutes"]
    if state.get("last_run_iso"):
        last_run = datetime.fromisoformat(state["last_run_iso"])
        minutes_elapsed = (now - last_run).total_seconds() / 60

    zones = config["nest"]["zones"]
    zone_statuses = {}
    total_estimated_cost = 0.0
    for zone in zones:
        thermostat = nest_control.get_thermostat_status(config["nest"], zone["device_id"])
        zone_statuses[zone["name"]] = thermostat
        total_estimated_cost += estimate_runtime_cost(
            thermostat, zone, config["duke_energy"]["rate_per_kwh"], minutes_elapsed
        )

    state["month_spend_dollars"] = state.get("month_spend_dollars", 0.0) + total_estimated_cost
    state["last_run_iso"] = now.isoformat()

    finalize_day_if_rolled_over(state, today.isoformat())
    update_day_accumulator(state, zone_statuses, today.isoformat())

    projection = budget_logic.project_month_end_cost(state, config, today)

    decisions = {}
    for zone in zones:
        thermostat = zone_statuses[zone["name"]]
        decision = budget_logic.decide_adjustment_for_zone(projection, thermostat, config)
        decisions[zone["name"]] = decision

        if decision["action"] == "nudge_cool":
            nest_control.nudge_setpoint(
                config["nest"], zone["device_id"], "COOL", decision["new_setpoint_f"]
            )
            notify.send(
                config,
                f"{zone['name']}: cooling setpoint raised",
                f"Now {decision['new_setpoint_f']}F. {decision['reason']}.",
            )
        elif decision["action"] == "nudge_heat":
            nest_control.nudge_setpoint(
                config["nest"], zone["device_id"], "HEAT", decision["new_setpoint_f"]
            )
            notify.send(
                config,
                f"{zone['name']}: heating setpoint lowered",
                f"Now {decision['new_setpoint_f']}F. {decision['reason']}.",
            )
        elif "comfort ceiling" in decision.get("reason", "") or "comfort floor" in decision.get("reason", ""):
            notify.send(config, f"{zone['name']}: at comfort limit", decision["reason"])

    state["zones"] = zone_statuses
    state["projection"] = projection
    state["decisions"] = decisions
    state["config_snapshot"] = {
        "budget": config["budget"]["monthly_target_dollars"],
        "cooling_ceiling_f": config["comfort"]["cooling_ceiling_f"],
        "heating_floor_f": config["comfort"]["heating_floor_f"],
        "step_size_f": config["budget"]["step_size_f"],
    }

    save_state(state)
    logger.info("Run complete: %s", decisions)


if __name__ == "__main__":
    main()
