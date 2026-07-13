"""Tests for the welcome sink: greets each channel name once, persisted,
at most one greeting per WELCOME_INTERVAL."""

import sqlite3
from pathlib import Path

from helpers import ReplyRecorder, channel_msg
from ottobot import IncomingMessage, Ottobot
from ottobot.config import BotConfig
from ottobot.sinks import register_module
from ottobot.sinks import welcome as welcome_module
from ottobot.sinks.welcome import WELCOME


async def make_welcome_bot(db_path: Path) -> Ottobot:
    """A bot with only the welcome sink loaded and its table created."""
    config = BotConfig(database=db_path)
    bot = Ottobot(name="ottobot", config=config)
    register_module(bot, welcome_module)
    await bot.setup()
    return bot


def chan(text: str, name: str | None, idx: int = 0) -> IncomingMessage:
    """A channel message from *name* (channel_msg hardcodes the name)."""
    return IncomingMessage(text=text, sender_name=name, channel_idx=idx)


async def test_first_channel_message_is_welcomed(
    tmp_path: Path, reply: ReplyRecorder
) -> None:
    bot = await make_welcome_bot(tmp_path / "seen.db")
    await bot.dispatch(channel_msg("hi all"), reply)  # sender_name="alice"
    assert reply.replies == [WELCOME]


async def test_repeat_from_same_name_is_silent(
    tmp_path: Path, reply: ReplyRecorder
) -> None:
    bot = await make_welcome_bot(tmp_path / "seen.db")
    await bot.dispatch(channel_msg("hi"), reply)
    await bot.dispatch(channel_msg("hi again"), reply)
    assert reply.replies == [WELCOME]


async def test_second_newcomer_in_quick_succession_is_rate_limited(
    tmp_path: Path, reply: ReplyRecorder
) -> None:
    # Only one welcome per WELCOME_INTERVAL: bob arrives right after alice,
    # so his greeting is held back (he'll get it on a later message — see
    # the _record tests below for the full timeline).
    bot = await make_welcome_bot(tmp_path / "seen.db")
    await bot.dispatch(chan("hi", "alice"), reply)
    await bot.dispatch(chan("hi", "bob"), reply)
    assert reply.replies == [WELCOME]


async def test_channel_message_without_a_name_is_ignored(
    tmp_path: Path, reply: ReplyRecorder
) -> None:
    bot = await make_welcome_bot(tmp_path / "seen.db")
    await bot.dispatch(chan("hi", None), reply)
    assert reply.replies == []


async def test_persists_across_restart(tmp_path: Path, reply: ReplyRecorder) -> None:
    db_path = tmp_path / "seen.db"
    first = await make_welcome_bot(db_path)
    await first.dispatch(channel_msg("hi"), reply)
    # A fresh bot on the same db file should not re-welcome a known name.
    second = await make_welcome_bot(db_path)
    await second.dispatch(channel_msg("hi again"), reply)
    assert reply.replies == [WELCOME]


async def test_non_public_channel_is_ignored(
    tmp_path: Path, reply: ReplyRecorder
) -> None:
    # The bot greets only on the public channel (index 0); a message on any
    # other channel it's joined must not be welcomed.
    bot = await make_welcome_bot(tmp_path / "seen.db")
    await bot.dispatch(chan("hi", "alice", idx=1), reply)
    assert reply.replies == []


async def test_setup_creates_the_table(tmp_path: Path) -> None:
    db_path = tmp_path / "seen.db"
    await make_welcome_bot(db_path)
    with sqlite3.connect(db_path) as conn:
        tables = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
    assert "seen_clients" in tables


def test_record_tracks_first_and_last_seen(tmp_path: Path) -> None:
    # _record's persistence semantics, with controlled timestamps.
    db_path = tmp_path / "seen.db"
    welcome_module._init_db(db_path)
    day1 = "2026-01-01T00:00:00+00:00"
    day2 = "2026-01-02T00:00:00+00:00"
    assert welcome_module._record(db_path, "alice", day1) is True
    assert welcome_module._record(db_path, "alice", day2) is False
    with sqlite3.connect(db_path) as conn:
        first_seen, last_seen = conn.execute(
            "SELECT first_seen, last_seen FROM seen_clients WHERE id = ?",
            ("alice",),
        ).fetchone()
    assert first_seen == day1  # unchanged on the repeat
    assert last_seen == day2  # bumped to the latest sighting


def test_record_rate_limits_welcomes_to_one_per_interval(tmp_path: Path) -> None:
    # bob shows up 30 minutes after alice's welcome: too soon, and he is
    # left unrecorded so a message after the cooldown still greets him.
    db_path = tmp_path / "seen.db"
    welcome_module._init_db(db_path)
    t0 = "2026-01-01T00:00:00+00:00"
    t0_plus_30m = "2026-01-01T00:30:00+00:00"
    t0_plus_61m = "2026-01-01T01:01:00+00:00"
    assert welcome_module._record(db_path, "alice", t0) is True
    assert welcome_module._record(db_path, "bob", t0_plus_30m) is False
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM seen_clients WHERE id = 'bob'"
        ).fetchone()[0]
    assert count == 0  # not recorded during the cooldown
    assert welcome_module._record(db_path, "bob", t0_plus_61m) is True


def test_record_cooldown_runs_from_the_last_welcome(tmp_path: Path) -> None:
    # The interval is measured from the most recent welcome, not the first:
    # carol arrives 30 minutes after bob's welcome and is held back even
    # though alice's welcome is well over an hour old.
    db_path = tmp_path / "seen.db"
    welcome_module._init_db(db_path)
    assert welcome_module._record(db_path, "alice", "2026-01-01T00:00:00+00:00")
    assert welcome_module._record(db_path, "bob", "2026-01-01T02:00:00+00:00")
    assert (
        welcome_module._record(db_path, "carol", "2026-01-01T02:30:00+00:00") is False
    )
