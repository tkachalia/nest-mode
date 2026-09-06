"""
Sends a push notification to your phone via ntfy.sh.

Setup: install the ntfy app (iOS/Android), pick a unique topic name
(e.g. "tia-nest-budget-8f2k"), subscribe to it in the app, and put that
same topic name in config.json. No account or API key needed.
"""

import logging
import requests

logger = logging.getLogger("notify")


def send(config: dict, title: str, message: str, priority: str = "default") -> None:
    topic = config["notifications"]["ntfy_topic"]
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Notification failed: %s", exc)
