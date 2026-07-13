"""Greets newcomers

Watches public (channel) messages and the first time it sees a given
sender name, it replies with a short welcome pointing at the #bots channel,
where the bot answers commands (it stays quiet on public — see
``channels.COMMAND_CHANNELS``). Seen names are stored in a sqlite file so
the bot doesn't re-greet people across restarts; each row also tracks when
the name was first seen and last heard from.

Welcomes are rate limited so a burst of newcomers doesn't flood the
channel: a newcomer is greeted only if no other newcomer was recorded in
the last ``WELCOME_INTERVAL``. Everyone is still recorded immediately, so
a newcomer who lands inside the cooldown simply never gets the greeting
(dropped, not deferred).
"""

from __future__ import annotations

import asyncio
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ottobot import Context, Ottobot, on_start, sink
from ottobot.channels import PUBLIC

# under 140 chars plz — and mention only #bots, since that's the one
# channel newcomers need to reach the bot (commands aren't answered here
# on public).
WELCOME = "Welcome to the mesh! To chat with me join the #bots channel and say '@ottobot !help'. More at https://ottawamesh.ca"

# A newcomer is greeted only if no other new name was recorded within this
# window (issue #80: don't greet too fast).
WELCOME_INTERVAL = timedelta(hours=1)


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
    """Record that *identifier* was just seen; return True if they should be welcomed.

    A known name just gets its last_seen bumped. A new name is always
    inserted right away, but greeted only when the previous newcomer's
    first_seen is at least WELCOME_INTERVAL old — the newest first_seen in
    the table is the rate-limit clock, so no extra column, in-memory state,
    or writes are needed. A newcomer who lands inside the cooldown is
    recorded like any other and therefore never greeted (welcomes are
    dropped, not deferred).
    """
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE seen_clients SET last_seen = ? WHERE id = ?",
            (now, identifier),
        )
        if cur.rowcount:  # already known — never re-welcomed
            return False
        (newest_first_seen,) = conn.execute(
            "SELECT MAX(first_seen) FROM seen_clients"
        ).fetchone()
        conn.execute(
            "INSERT INTO seen_clients (id, first_seen, last_seen) VALUES (?, ?, ?)",
            (identifier, now, now),
        )
    if newest_first_seen is None:
        return True
    elapsed = datetime.fromisoformat(now) - datetime.fromisoformat(newest_first_seen)
    return elapsed >= WELCOME_INTERVAL


@on_start()
async def setup(bot: Ottobot) -> None:
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
    should_welcome = await asyncio.to_thread(_record, ctx.config.database, name, now)

    if should_welcome:
        return WELCOME
