"""Poll Environment Canada's weather alert feed for Ottawa.

Environment Canada publishes an Atom "battleboard" feed of currently
active weather alerts (warnings, watches, statements) per region. Every 10
minutes the feed is fetched and any alert not seen on a previous fetch is
announced, one message per new alert. The very first
fetch only records what's already active — it
doesn't announce ongoing alerts the bot just happened to start during — so
only newly issued alerts are ever announced. Alerts go out on the
"#ott-alerts" channel, one of the configured channels (ottobot.channels).

The feed represents "no alerts" as a real <entry> ("No alerts in effect,
..."), so the bot announces an all-clear once when the last alert ends.
That entry's <id> embeds the feed's update timestamp, so this relies on
Environment Canada only bumping the timestamp when the battleboard
actually changes. If the all-clear ever starts repeating, dedupe it by
title instead of id (announce it only when the previous announcement
wasn't already an all-clear).

To be nice to Environment Canada's servers, fetches are conditional: the
ETag/Last-Modified validators from the last successful fetch are sent back
as If-None-Match/If-Modified-Since, so an unchanged feed costs a single
304 with no body. The validators live in memory only (no disk writes); a
restart just means one unconditional fetch to re-prime them.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from xml.etree import ElementTree

import httpx

from ottobot import TaskContext, task
from ottobot.channels import OTT_ALERTS

logger = logging.getLogger(__name__)

ALERTS_URL = "https://weather.gc.ca/rss/battleboard/onrm104_e.xml"

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# Every entry title in the regional feed ends with the region name; drop it
# to save mesh bandwidth.
_TITLE_SUFFIX = ", Ottawa North - Kanata - Orléans"

# The battleboard packs active alerts into numbered slots (onrm104_w1,
# onrm104_w2, …) ordered newest-first, and an entry's <id> embeds its slot.
# So when a new alert is issued the older ones shift down a slot and their
# ids change — a reshuffle that must not look like a brand-new alert.
_SLOT_RE = re.compile(r"_w\d+(?=:)")

# Stable, slot-independent keys of alerts already announced or seen on the
# priming run.
_seen: set[str] = set()
_primed = False

# Cache validators from the last successfully parsed fetch, kept in memory
# only. Either may be None if the server didn't send it.
_etag: str | None = None
_last_modified: str | None = None


def normalize_etag(etag: str) -> str:
    """Strip Apache mod_deflate's "-gzip" ETag suffix.

    Apache tags compressed responses with `W/"...-gzip"` but only matches
    If-None-Match against the uncompressed form, so sending the suffixed
    value back gets a full 200 every time. The stripped form matches
    (verified against weather.gc.ca).
    """
    if etag.endswith('-gzip"'):
        return etag[: -len('-gzip"')] + '"'
    return etag


def alert_key(alert_id: str) -> str:
    """Identify an alert independently of the feed slot it currently occupies.

    Dropping the `_wN` slot marker leaves the region and issue timestamp,
    which stay put when other alerts come and go — so an ongoing alert that
    merely shifted slots isn't mistaken for a new one. A genuine re-issue
    changes the timestamp and so is still announced again. The "no alerts"
    all-clear entry has no slot marker and is returned unchanged.
    """
    return _SLOT_RE.sub("", alert_id)


def parse_alerts(xml_text: str) -> list[tuple[str, str]]:
    """Return (id, title) for each <entry> in the alerts feed, document order."""
    root = ElementTree.fromstring(xml_text)
    alerts = []
    for entry in root.findall("atom:entry", _ATOM_NS):
        title = (
            entry.findtext("atom:title", default="", namespaces=_ATOM_NS) or ""
        ).strip()
        title = title.removesuffix(_TITLE_SUFFIX)
        alert_id = (
            entry.findtext("atom:id", default="", namespaces=_ATOM_NS) or ""
        ).strip()
        if alert_id:
            alerts.append((alert_id, title or alert_id))
    return alerts


@task(
    "weather_alerts",
    interval=timedelta(minutes=10),
    channel=OTT_ALERTS,
    help="Announce new Environment Canada weather alerts for Ottawa",
)
async def weather_alerts(ctx: TaskContext) -> None:
    global _primed, _etag, _last_modified
    headers = {}
    if _etag is not None:
        headers["If-None-Match"] = _etag
    if _last_modified is not None:
        headers["If-Modified-Since"] = _last_modified
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(ALERTS_URL, headers=headers)
            if response.status_code == 304:
                return
            response.raise_for_status()
        alerts = parse_alerts(response.text)
        # Only keep validators for a feed we actually parsed, so a bad
        # document can't get stuck behind 304s.
        etag = response.headers.get("ETag")
        _etag = normalize_etag(etag) if etag is not None else None
        _last_modified = response.headers.get("Last-Modified")
    except Exception:
        logger.warning("failed to fetch Environment Canada alerts", exc_info=True)
        return

    if not _primed:
        _seen.update(alert_key(alert_id) for alert_id, _ in alerts)
        _primed = True
        return

    new_alerts = [pair for pair in alerts if alert_key(pair[0]) not in _seen]
    # Announce oldest-first (the feed lists newest first), one message per
    # alert.
    for alert_id, title in reversed(new_alerts):
        _seen.add(alert_key(alert_id))
        await ctx.reply(title)
    # Drop keys that have left the feed so _seen doesn't grow forever on a
    # long-running bot. Keys embed their issue timestamp, so an ended
    # alert's key doesn't come back; a genuinely re-issued alert gets a
    # fresh key and is announced again.
    _seen.intersection_update(alert_key(alert_id) for alert_id, _ in alerts)
