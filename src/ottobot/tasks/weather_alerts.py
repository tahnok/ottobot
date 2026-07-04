"""Poll Environment Canada's weather alert feed for Ottawa.

Environment Canada publishes an Atom feed of currently active weather
alerts (warnings, watches, statements) keyed to a lat/long. Every 10
minutes the feed is fetched and any alert not seen on a previous fetch is
announced. The very first fetch only records what's already active — it
doesn't announce ongoing alerts the bot just happened to start during — so
only newly issued alerts are ever announced. Alerts go out on the
"#ott-alerts" channel, which must be in the config's [[channels]].
"""

from __future__ import annotations

import logging
from datetime import timedelta
from xml.etree import ElementTree

import httpx

from ottobot import TaskContext, task

logger = logging.getLogger(__name__)

# Ottawa
ALERTS_URL = "https://weather.gc.ca/rss/alerts/45.403_-75.687_e.xml"

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# ids of alerts already announced or seen on the priming run.
_seen: set[str] = set()
_primed = False


def parse_alerts(xml_text: str) -> list[tuple[str, str]]:
    """Return (id, title) for each <entry> in the alerts feed, document order."""
    root = ElementTree.fromstring(xml_text)
    alerts = []
    for entry in root.findall("atom:entry", _ATOM_NS):
        title = (
            entry.findtext("atom:title", default="", namespaces=_ATOM_NS) or ""
        ).strip()
        alert_id = (
            entry.findtext("atom:id", default="", namespaces=_ATOM_NS) or ""
        ).strip()
        if alert_id:
            alerts.append((alert_id, title or alert_id))
    return alerts


@task(
    "weather_alerts",
    interval=timedelta(minutes=10),
    channel="#ott-alerts",
    help="Announce new Environment Canada weather alerts for Ottawa",
)
async def weather_alerts(ctx: TaskContext) -> None:
    global _primed
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(ALERTS_URL)
            response.raise_for_status()
        alerts = parse_alerts(response.text)
    except Exception:
        logger.warning("failed to fetch Environment Canada alerts", exc_info=True)
        return

    if not _primed:
        _seen.update(alert_id for alert_id, _ in alerts)
        _primed = True
        return

    new_alerts = [pair for pair in alerts if pair[0] not in _seen]
    for alert_id, title in reversed(new_alerts):
        _seen.add(alert_id)
        await ctx.reply(title)
