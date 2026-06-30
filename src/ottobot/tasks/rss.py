"""Poll an RSS feed and announce new items.

Set the feed URL in the config to enable it::

    [rss]
    url = "https://example.com/feed.xml"

The runner calls this every 30 minutes (see @task below). Each run fetches
the feed and announces any items not seen on a previous run, oldest first.
The very first run only records what's already there — it doesn't replay
the feed's whole history — so only items published after the bot starts
are ever announced. With no url configured the task is inert.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from xml.etree import ElementTree

import httpx

from ottobot import TaskContext, task

logger = logging.getLogger(__name__)

# guids (or links, when a feed omits guid) of items already announced or
# seen on the priming run. Module-level because @task handlers are bare
# functions, not bound to any per-bot instance.
_seen: set[str] = set()
_primed = False


def parse_items(xml_text: str) -> list[tuple[str, str]]:
    """Return (id, title) for each <item> in an RSS 2.0 feed, document order.

    id is the item's <guid>, falling back to its <link> when a feed omits
    guid. Feeds conventionally list the newest item first.
    """
    root = ElementTree.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip() or link
        if guid:
            items.append((guid, title or link))
    return items


@task("rss", interval=timedelta(minutes=30), help="Announce new RSS feed items")
async def rss(ctx: TaskContext) -> None:
    global _primed
    url = ctx.config.rss_feed_url
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
        items = parse_items(response.text)
    except Exception:
        logger.warning("failed to fetch RSS feed %r", url, exc_info=True)
        return

    if not _primed:
        _seen.update(guid for guid, _ in items)
        _primed = True
        return

    new_items = [pair for pair in items if pair[0] not in _seen]
    for guid, title in reversed(new_items):
        _seen.add(guid)
        await ctx.reply(title)
