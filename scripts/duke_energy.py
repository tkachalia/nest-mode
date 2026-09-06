"""
Pulls Duke Energy usage.

Uses the unofficial pydukeenergy/pyduke-energy library, which logs in the
same way the Duke Energy app does. This is NOT an official API and could
break if Duke changes their backend without notice.

Fallback: if the library call fails, this reads a manually-entered value
from state.json (manual_kwh_override) so the tool keeps working even if
the scrape breaks. Just log into the Duke app yourself and type the
number in when that happens.
"""

import asyncio
import logging

logger = logging.getLogger("duke_energy")


async def get_yesterdays_kwh(config: dict, state: dict) -> float | None:
    """
    Returns yesterday's total home kWh usage, or None if unavailable.
    """
    override = state.get("manual_kwh_override")
    if override is not None:
        logger.info("Using manual kWh override: %s", override)
        return float(override)

    try:
        from pyduke_energy.client import DukeEnergyClient
        import aiohttp

        email = config["duke_energy"]["email"]
        password = config["duke_energy"]["password"]

        async with aiohttp.ClientSession() as session:
            client = DukeEnergyClient(email, password, session)
            await client.authenticate()
            await client.select_default_meter()
            usage = await client.get_usage_yesterday()
            return float(usage.total_kwh)

    except Exception as exc:
        logger.warning(
            "Duke Energy fetch failed (%s). Falling back to no data for "
            "this run. Set 'manual_kwh_override' in state.json to unblock.",
            exc,
        )
        return None


def get_yesterdays_kwh_sync(config: dict, state: dict) -> float | None:
    return asyncio.run(get_yesterdays_kwh(config, state))


# --- Historical backfill ---
#
# The unofficial library only reliably supports "yesterday's" usage, not a
# documented range query, so automated backfill of a full year is not
# something we can guarantee. Duke's own account portal DOES show up to
# 24 months of past billing history to a logged-in user though, so the
# supported path for backfill is: you copy your past monthly totals from
# https://www.duke-energy.com (My Account > Billing & Payment History)
# into backfill_history_import.csv, and scripts/backfill_history.py loads
# that CSV into dashboard/history.json. See that script for the format.
