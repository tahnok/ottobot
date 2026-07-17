"""Greets newcomers

Watches public (channel) messages and the first time it sees a given
sender name, it replies with a short welcome pointing at the #bots channel,
where the bot answers commands (it stays quiet on public — see
``channels.COMMAND_CHANNELS``). Seen names are stored in a sqlite file so
the bot doesn't re-greet people across restarts; each row also tracks when
the name was first seen and last heard from.

Welcomes are rate limited to at most one per ``WELCOME_INTERVAL``,
measured from the previous greeting, so a burst of newcomers doesn't
flood the channel. The last-greeting clock is kept in memory (greetings
are rare; no point writing it to disk) and primed from the newest
first_seen in the table after a restart. Everyone is still recorded
immediately, so a newcomer who lands inside the cooldown simply never
gets the greeting (dropped, not deferred).

Greetings are scoped to the local mesh: messages that arrived over more
than ``WELCOME_MAX_HOPS`` repeater hops are ignored outright — not even
recorded. Unlike the cooldown, this defers rather than drops: a faraway
newcomer still gets the welcome the first time they're heard from within
range.
"""

from __future__ import annotations

import asyncio
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ottobot import Context, IncomingMessage, Ottobot, on_start, sink
from ottobot.channels import PUBLIC

# under 140 chars plz — and mention only #bots, since that's the one
# channel newcomers need to reach the bot (commands aren't answered here
# on public).
WELCOME = "Welcome to the mesh! To chat with me join the #bots channel and say '@ottobot !help'. More (incl. Discord/Matrix) at https://ottawamesh.ca"

# Minimum time between two welcome messages (issue #80: don't greet too fast).
WELCOME_INTERVAL = timedelta(hours=1)

# Only greet nodes that are network-local: at most this many repeater hops
# away. Messages with an unknown path (hop_count is None) are treated as in
# range — real transports always report path_len.
WELCOME_MAX_HOPS = 5

# When the bot last greeted anyone — the rate-limit clock. Primed from the
# newest first_seen in the database on the first newcomer after a restart;
# that timestamp can belong to an ungreeted newcomer, so at worst the first
# post-restart greeting is delayed by one extra interval. A single global is
# enough: a process runs one bot against one database (tests reset it via
# the fresh_welcome_clock fixture).
_last_welcome: datetime | None = None


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
    inserted right away, but greeted only when the last greeting (the
    _last_welcome clock) is at least WELCOME_INTERVAL old — ungreeted
    arrivals don't push the clock, so a steady trickle of newcomers still
    gets about one greeting per interval. A newcomer who lands inside the
    cooldown is recorded like any other and therefore never greeted
    (welcomes are dropped, not deferred).
    """
    global _last_welcome
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE seen_clients SET last_seen = ? WHERE id = ?",
            (now, identifier),
        )
        if cur.rowcount:  # already known — never re-welcomed
            return False
        if _last_welcome is None:
            (newest_first_seen,) = conn.execute(
                "SELECT MAX(first_seen) FROM seen_clients"
            ).fetchone()
            if newest_first_seen is not None:
                _last_welcome = datetime.fromisoformat(newest_first_seen)
        conn.execute(
            "INSERT INTO seen_clients (id, first_seen, last_seen) VALUES (?, ?, ?)",
            (identifier, now, now),
        )
    now_dt = datetime.fromisoformat(now)
    if _last_welcome is not None and now_dt - _last_welcome < WELCOME_INTERVAL:
        return False
    _last_welcome = now_dt
    return True


def _should_greet(message: IncomingMessage) -> bool:
    """Whether *message* is in scope for a greeting at all.

    Only the public channel, and only network-local senders — within
    WELCOME_MAX_HOPS repeater hops (an unknown path counts as local).
    Out-of-scope messages are ignored outright, before any recording.
    """
    if message.channel_idx != PUBLIC.index:
        return False
    hops = message.hop_count
    return hops is None or hops <= WELCOME_MAX_HOPS


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
    if not _should_greet(ctx.message):
        return

    now = datetime.now(timezone.utc).isoformat()
    should_welcome = await asyncio.to_thread(_record, ctx.config.database, name, now)

    if should_welcome:
        return WELCOME
