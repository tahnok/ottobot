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

The bbox query returns one GeoJSON Feature per polygon of an alert that
intersects Ottawa, so a single alert spanning several polygons shows up as
several Features that share one weather bulletin. They're deduped on the
bulletin id (see ``alert_key``), and then on the announced text, since one
weather event reaches Ottawa as several bulletins — one per region, e.g. an
Ontario-side and a Gatineau special weather statement carrying the same
headline. Alerts that have ended linger in the collection for hours and
are ignored (see ``ENDED``), so when the last alert ends the collection
reads as empty and the bot announces an all-clear once (guarded by
``_seen`` so it can't repeat).
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
# `properties` restricts the response to the fields we read — without them
# each Feature also carries its full boundary polygon and the French half
# of every bulletin, a multiple of the payload we'd only throw away.
_PARAMS = {
    "bbox": "-76.1,45.15,-75.4,45.55",
    "f": "json",
    "limit": 100,
    "skipGeometry": "true",
    "properties": (
        "id,feature_id,alert_name_en,alert_code,publication_datetime,"
        "alert_text_en,status_en"
    ),
}

# An alert that's over stays in the collection until its expiration
# datetime, up to about a day later, carrying status_en "ended" (the live
# values are "issued", "continued" and "ended"). Those are dropped: nothing
# is announced for weather that's already past, and the collection reads as
# empty as soon as the last real alert ends, so the all-clear goes out then
# rather than a day late.
ENDED = "ended"

# MeshCore truncates a channel message past ~140 UTF-8 bytes, so an alert
# only carries its headline when the two together stay under that.
MAX_MESSAGE_LEN = 140

# Announced once when the last active alert clears: the empty collection
# carries no entry to announce, so the message is synthesized here.
ALL_CLEAR = "No alerts in effect"

# Announcements already made or seen on the priming run, held as the text
# announced rather than the bulletin id: an alert re-issued per region
# arrives as several bulletins carrying one message, and the channel should
# see that message once.
_seen: set[str] = set()
_primed = False


class Alert(NamedTuple):
    key: str  # stable per-alert key (the bulletin id), for polygon dedup
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


def headline(alert_text: str) -> str:
    """The one-line summary an alert bulletin opens with, if it has one.

    ``alert_text_en`` is the full bulletin: usually a plain-language
    headline paragraph ("Heavy rainfall possible through Wednesday
    morning.") followed by labelled sections — "What:", "When:",
    "Additional information:". Some bulletins open straight at a label, and
    then the first line under it is the closest thing to a summary.
    """
    lines = [line.strip() for line in alert_text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    if not lines[0].endswith(":"):
        return lines[0]
    return lines[1] if len(lines) > 1 else ""


def _title(props: dict[str, Any], key: str) -> str:
    """The text announced for an alert, e.g. "Air Quality Warning".

    The alert name alone is often the whole story ("Tornado Warning"), but
    a name like "Special Weather Statement" says nothing about the weather,
    so Environment Canada's own headline is appended when the bulletin
    carries one — as long as the result still fits a packet. The headlines
    that don't fit are the long boilerplate ones ("Conditions are
    favourable for the development of severe thunderstorms that…"), whose
    clipped half is worth less on the channel than the bare name.
    """
    name = (props.get("alert_name_en") or "").strip()
    title = name.title() if name else (props.get("alert_code") or key)
    summary = headline(props.get("alert_text_en") or "")
    if not summary:
        return title
    with_summary = f"{title}: {summary}"
    if len(with_summary.encode("utf-8")) > MAX_MESSAGE_LEN:
        return title
    return with_summary


def parse_alerts(payload: dict[str, Any]) -> list[Alert]:
    """Return one Alert per distinct announcement in effect, oldest-first.

    Alerts that have ended are dropped. What's left collapses twice: first
    on the bulletin id, so the several polygons of one alert become one
    Alert, then on the announced text, so the per-region bulletins of one
    weather event become one message rather than several identical ones.
    The result is ordered by publication time so several alerts found in
    one fetch are announced oldest-first.
    """
    by_key: dict[str, Alert] = {}
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        alert_id = (props.get("id") or "").strip()
        if not alert_id or props.get("status_en") == ENDED:
            continue
        key = alert_key(alert_id, props.get("feature_id"))
        by_key.setdefault(
            key,
            Alert(key, _title(props, key), props.get("publication_datetime") or ""),
        )
    by_title: dict[str, Alert] = {}
    for alert in sorted(by_key.values(), key=lambda a: (a.published, a.key)):
        by_title.setdefault(alert.title, alert)
    return list(by_title.values())


async def fetch_alerts() -> list[Alert]:
    """Fetch the live Ottawa collection and parse it into announcements."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(ALERTS_URL, params=_PARAMS)
        response.raise_for_status()
        payload = response.json()
    return parse_alerts(payload)


@task(
    "weather_alerts",
    interval=timedelta(minutes=10),
    channel=OTT_ALERTS,
    help="Announce new Environment Canada weather alerts for Ottawa",
)
async def weather_alerts(ctx: TaskContext) -> None:
    global _primed
    try:
        alerts = await fetch_alerts()
    except Exception:
        logger.warning("failed to fetch Environment Canada alerts", exc_info=True)
        return

    logger.info(f"alerts from feed {alerts}")

    if not _primed:
        _seen.update(alert.title for alert in alerts)
        _primed = True
        return

    new_alerts = [alert for alert in alerts if alert.title not in _seen]
    # Several alerts can appear in one fetch; announce each on its own
    # line/packet, oldest-first (parse_alerts already orders them).
    await ctx.reply_many(alert.title for alert in new_alerts)
    # The collection just went empty after having alerts: sound the
    # all-clear once. The _seen guard (cleared below) keeps it from
    # repeating on subsequent empty fetches.
    if not alerts and _seen:
        await ctx.reply(ALL_CLEAR)
    # Track only the live collection so _seen doesn't grow forever on a
    # long-running bot. An ended alert's text doesn't come back; a re-issued
    # alert saying something new is announced again.
    _seen.clear()
    _seen.update(alert.title for alert in alerts)
