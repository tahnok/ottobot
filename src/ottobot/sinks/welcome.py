"""Greets newcomers to a channel.

Watches public (channel) messages and the first time it sees a given
sender name, it replies with a short welcome pointing at !help. Seen names
are stored in a sqlite file so the bot doesn't re-greet people across
restarts; each row also tracks when the name was first seen and last heard
from.

Senders are tracked by name only — channel messages carry no public key,
and DMs are skipped entirely. The name is spoofable, so this is a
courtesy, not anything to rely on.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ottobot import Context, MeshBot, on_start, sink

WELCOME = "Welcome to the Ottawa mesh! Send !help to see what I can do."


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
    if ctx.message.is_dm:  # only greet on channels, never in DMs
        return None
    name = ctx.sender_name
    if not name:  # channel message with no recoverable name
        return None
    if not ctx.config.database:
        return
    now = datetime.now(timezone.utc).isoformat()
    is_new = await asyncio.to_thread(_record, ctx.config.database, name, now)
    return WELCOME if is_new else None
