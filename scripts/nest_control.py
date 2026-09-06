"""
Talks to Google's Smart Device Management (SDM) API to read your Nest's
current state and adjust its setpoint in small steps.

Setup required once, outside this script:
1. Register a Device Access project at https://console.nest.google.com/device-access
   (one-time $5 fee).
2. Link it to your Google account and complete the OAuth consent flow to
   get a refresh_token. Google's SDM quickstart walks through this.
3. Put project_id, client_id, client_secret, refresh_token, and your
   thermostat's device_id into config.json.
"""

import logging
import requests

logger = logging.getLogger("nest_control")

OAUTH_URL = "https://www.googleapis.com/oauth2/v4/token"
SDM_BASE = "https://smartdevicemanagement.googleapis.com/v1"


def _get_access_token(nest_cfg: dict) -> str:
    resp = requests.post(
        OAUTH_URL,
        data={
            "client_id": nest_cfg["client_id"],
            "client_secret": nest_cfg["client_secret"],
            "refresh_token": nest_cfg["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_thermostat_status(nest_cfg: dict, device_id: str) -> dict:
    """
    Returns a dict with: mode ('COOL'/'HEAT'/'OFF'), current setpoint(s) in F,
    ambient temp in F, and hvac_status ('COOLING'/'HEATING'/'OFF').
    """
    token = _get_access_token(nest_cfg)
    device_path = f"enterprises/{nest_cfg['project_id']}/devices/{device_id}"
    resp = requests.get(
        f"{SDM_BASE}/{device_path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    traits = resp.json().get("traits", {})

    def c_to_f(c):
        return round(c * 9 / 5 + 32, 1)

    mode = traits.get("sdm.devices.traits.ThermostatMode", {}).get("mode", "OFF")
    hvac_status = traits.get("sdm.devices.traits.ThermostatHvac", {}).get(
        "status", "OFF"
    )
    ambient_c = traits.get("sdm.devices.traits.Temperature", {}).get(
        "ambientTemperatureCelsius"
    )
    setpoint_traits = traits.get("sdm.devices.traits.ThermostatTemperatureSetpoint", {})

    return {
        "mode": mode,
        "hvac_status": hvac_status,
        "ambient_f": c_to_f(ambient_c) if ambient_c is not None else None,
        "heat_setpoint_f": (
            c_to_f(setpoint_traits["heatCelsius"])
            if "heatCelsius" in setpoint_traits
            else None
        ),
        "cool_setpoint_f": (
            c_to_f(setpoint_traits["coolCelsius"])
            if "coolCelsius" in setpoint_traits
            else None
        ),
    }


def nudge_setpoint(nest_cfg: dict, device_id: str, mode: str, new_setpoint_f: float) -> None:
    """
    mode: 'COOL' or 'HEAT'. Sends a single SetCool/SetHeat command with the
    new setpoint, converted to Celsius as the API requires.
    """
    token = _get_access_token(nest_cfg)
    device_path = f"enterprises/{nest_cfg['project_id']}/devices/{device_id}"
    new_setpoint_c = round((new_setpoint_f - 32) * 5 / 9, 1)

    command = (
        "sdm.devices.commands.ThermostatTemperatureSetpoint.SetCool"
        if mode == "COOL"
        else "sdm.devices.commands.ThermostatTemperatureSetpoint.SetHeat"
    )
    field = "coolCelsius" if mode == "COOL" else "heatCelsius"

    resp = requests.post(
        f"{SDM_BASE}/{device_path}:executeCommand",
        headers={"Authorization": f"Bearer {token}"},
        json={"command": command, "params": {field: new_setpoint_c}},
        timeout=15,
    )
    resp.raise_for_status()
    logger.info("Set %s setpoint to %.1fF", mode, new_setpoint_f)
