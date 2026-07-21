"""Poll Environment Canada's weather alerts API for Ottawa.

Environment Canada's modern alerts API
(``api.weather.gc.ca/collections/weather-alerts``) publishes every active
weather alert (warnings, watches, statements) as GeoJSON. We query it
filtered to an Ottawa ``bbox``; every 10 minutes the collection is fetched
and any alert not seen on a previous fetch is announced, one message per
new alert. The very first fetch only records what's already active — it
doesn't announce ongoing alerts the bot just happened to start during — so
only newly issued alerts are ever announced. Alerts go out on the
"#ott-alerts" channel, one of the configured channels (ottobot.channels).

This API is a superset of the older "battleboard" RSS feed the task used
to poll: crucially it carries **air quality warnings** (``AQW``), which the
battleboard omitted, so smoke/air-quality warnings now reach the channel.

The bbox query returns one GeoJSON Feature per polygon of an alert that
intersects Ottawa, so a single alert spanning several polygons shows up as
several Features that share one weather bulletin. They're deduped on the
bulletin id (see ``alert_key``) so each alert is announced once. When the
last alert ends the collection goes empty, and the bot announces an
all-clear once (guarded by ``_seen`` so it can't repeat).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, NamedTuple

import httpx

from ottobot import TaskContext, task
from ottobot.channels import OTT_ALERTS

logger = logging.getLogger(__name__)

ALERTS_URL = "https://api.weather.gc.ca/collections/weather-alerts/items"

# The Ottawa region. bbox is min-lon,min-lat,max-lon,max-lat; limit is set
# well above the handful of alert polygons Ottawa ever sees at once so a
# real alert can't be truncated off the end of the collection (the API
# default is only 10). skipGeometry drops the alert polygons and
# `properties` restricts the response to the handful of fields we read —
# without them each Feature also carries its full boundary polygon and
# multi-paragraph bilingual alert_text, ~75x more payload we'd only throw
# away.
_PARAMS = {
    "bbox": "-76.1,45.15,-75.4,45.55",
    "f": "json",
    "limit": 100,
    "skipGeometry": "true",
    "properties": "id,feature_id,alert_name_en,alert_code,publication_datetime",
}

# Announced once when the last active alert clears (the empty collection
# carries no entry to announce, unlike the old battleboard's "No alerts"
# entry, so the message is synthesized here).
ALL_CLEAR = "No alerts in effect"

# Stable, polygon-independent keys of alerts already announced or seen on
# the priming run.
_seen: set[str] = set()
_primed = False


class Alert(NamedTuple):
    key: str  # stable per-alert dedup key (the bulletin id)
    title: str  # human text announced on the channel
    published: str  # publication_datetime, for oldest-first ordering


def alert_key(alert_id: str, feature_id: str | None) -> str:
    """The stable per-alert id, independent of which polygon carried it.

    The bbox query returns one Feature per polygon of an alert that
    intersects Ottawa, all sharing a single weather bulletin but each with
    its own ``feature_id`` appended to the Feature ``id`` (e.g.
    ``<bulletin>_fea1-2370``). Stripping that suffix leaves the bulletin
    id, which is identical across every polygon of the same alert, so an
    alert spanning several polygons is announced once. A re-issued alert
    gets a fresh bulletin id and so is announced again.
    """
    if feature_id:
        return alert_id.removesuffix("_" + feature_id)
    return alert_id


def _title(props: dict[str, Any], key: str) -> str:
    """Short channel text for an alert, e.g. "Air Quality Warning"."""
    name = (props.get("alert_name_en") or "").strip()
    if name:
        return name.title()
    return props.get("alert_code") or key


def parse_alerts(payload: dict[str, Any]) -> list[Alert]:
    """Return one Alert per distinct alert, deduped and oldest-first.

    Features that share a bulletin id (the same alert seen as several
    polygons) collapse to a single Alert. The result is ordered by
    publication time so several alerts found in one fetch are announced
    oldest-first.
    """
    by_key: dict[str, Alert] = {}
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        alert_id = (props.get("id") or "").strip()
        if not alert_id:
            continue
        key = alert_key(alert_id, props.get("feature_id"))
        by_key.setdefault(
            key,
            Alert(key, _title(props, key), props.get("publication_datetime") or ""),
        )
    return sorted(by_key.values(), key=lambda a: (a.published, a.key))


@task(
    "weather_alerts",
    interval=timedelta(minutes=10),
    channel=OTT_ALERTS,
    help="Announce new Environment Canada weather alerts for Ottawa",
)
async def weather_alerts(ctx: TaskContext) -> None:
    global _primed
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(ALERTS_URL, params=_PARAMS)
            response.raise_for_status()
            payload = response.json()
        alerts = parse_alerts(payload)
    except Exception:
        logger.warning("failed to fetch Environment Canada alerts", exc_info=True)
        return

    if not _primed:
        _seen.update(alert.key for alert in alerts)
        _primed = True
        return

    new_alerts = [alert for alert in alerts if alert.key not in _seen]
    # Several alerts can appear in one fetch; announce each on its own
    # line/packet, oldest-first (parse_alerts already orders them).
    await ctx.reply_many(alert.title for alert in new_alerts)
    # The collection just went empty after having alerts: sound the
    # all-clear once. The _seen guard (cleared below) keeps it from
    # repeating on subsequent empty fetches.
    if not alerts and _seen:
        await ctx.reply(ALL_CLEAR)
    # Track only the live collection so _seen doesn't grow forever on a
    # long-running bot. An ended alert's key doesn't come back; a genuinely
    # re-issued alert gets a fresh key and is announced again.
    _seen.clear()
    _seen.update(alert.key for alert in alerts)
