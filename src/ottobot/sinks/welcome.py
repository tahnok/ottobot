"""Greets newcomers

Watches public (channel) messages and the first time it sees a given
sender name, it replies with a short welcome pointing at the #bots channel,
where the bot answers commands (it stays quiet on public — see
``channels.COMMAND_CHANNELS``). Seen names are stored in a sqlite file so
the bot doesn't re-greet people across restarts; each row also tracks when
the name was first seen and last heard from.

Welcomes are rate limited to one per ``WELCOME_INTERVAL`` so a burst of
newcomers doesn't flood the channel. A newcomer who arrives during the
cooldown isn't recorded yet, so a later message from them (once the
cooldown is over) still gets the greeting.
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

# Minimum time between two welcome messages (issue #80: don't greet too fast).
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

    A known name just gets its last_seen bumped. A new name is inserted —
    and welcomed — only when the previous welcome is at least
    WELCOME_INTERVAL old; rows are only ever inserted at welcome time, so
    the newest first_seen in the table *is* the last welcome time, and the
    rate limit costs no extra writes or columns. During the cooldown the
    newcomer is left unrecorded so a later message from them still triggers
    the greeting.
    """
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE seen_clients SET last_seen = ? WHERE id = ?",
            (now, identifier),
        )
        if cur.rowcount:  # already known — never re-welcomed
            return False
        (last_welcome,) = conn.execute(
            "SELECT MAX(first_seen) FROM seen_clients"
        ).fetchone()
        if last_welcome is not None:
            elapsed = datetime.fromisoformat(now) - datetime.fromisoformat(last_welcome)
            if elapsed < WELCOME_INTERVAL:
                return False
        conn.execute(
            "INSERT INTO seen_clients (id, first_seen, last_seen) VALUES (?, ?, ?)",
            (identifier, now, now),
        )
    return True


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
