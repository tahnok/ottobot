"""Greets newcomers

Watches public (channel) messages and the first time it sees a given
sender name, it replies with a short welcome pointing at !help. Seen names
are stored in a sqlite file so the bot doesn't re-greet people across
restarts; each row also tracks when the name was first seen and last heard
from.
"""

from __future__ import annotations

import asyncio
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

from ottobot import Context, MeshBot, on_start, sink
from ottobot.channels import PUBLIC

# under 140 chars plz
WELCOME = "Welcome to the mesh! Say '@ottobot !channels' or !help for more from me. See also https://ottawamesh.ca"


logger = logging.getLogger(__name__)


def _init_db(db_path: Path) -> None:
    """Create the seen-clients table if it doesn't exist yet."""
    if db_path.parent != Path(""):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS seen_clients ("
            "id TEXT PRIMARY KEY, "
            "first_seen TEXT NOT NULL, "
            "last_seen TEXT NOT NULL)"
        )


def _record(db_path: Path, identifier: str, now: str) -> bool:
    """Record that *identifier* was just seen; return True if it's the first time.

    A single upsert: inserts a new row on first sight, otherwise bumps
    last_seen and leaves first_seen alone. RETURNING gives back first_seen,
    which equals *now* only when the row was just inserted (on a repeat it's
    the older original timestamp), so that's how we detect a newcomer.
    """
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO seen_clients (id, first_seen, last_seen) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_seen = excluded.last_seen "
            "RETURNING first_seen",
            (identifier, now, now),
        )
        first_seen = cur.fetchone()[0]
    return first_seen == now


@on_start()
async def setup(bot: MeshBot) -> None:
    if not bot.config.database:
        return
    await asyncio.to_thread(_init_db, bot.config.database)


@sink()
async def welcome(ctx: Context) -> str | None:
    name = ctx.sender_name
    if not name:  # channel message with no recoverable name
        return None
    if not ctx.config.database:
        return

    if ctx.message.channel_idx != PUBLIC.index:
        return
    now = datetime.now(timezone.utc).isoformat()
    is_new = await asyncio.to_thread(_record, ctx.config.database, name, now)

    if is_new:
        return WELCOME
