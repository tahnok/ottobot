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
    # bob arrives right after alice, inside the cooldown: he is recorded
    # like any other newcomer but his greeting is dropped for good.
    db_path = tmp_path / "seen.db"
    bot = await make_welcome_bot(db_path)
    await bot.dispatch(chan("hi", "alice"), reply)
    await bot.dispatch(chan("hi", "bob"), reply)
    assert reply.replies == [WELCOME]
    with sqlite3.connect(db_path) as conn:
        names = {row[0] for row in conn.execute("SELECT id FROM seen_clients")}
    assert names == {"alice", "bob"}  # recorded right away despite no greeting


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
    # bob shows up 30 minutes after alice: too soon. He is recorded right
    # away anyway, and the missed greeting is dropped — a later message
    # from him is just a repeat from a known name.
    db_path = tmp_path / "seen.db"
    welcome_module._init_db(db_path)
    t0 = "2026-01-01T00:00:00+00:00"
    t0_plus_30m = "2026-01-01T00:30:00+00:00"
    t0_plus_2h = "2026-01-01T02:00:00+00:00"
    assert welcome_module._record(db_path, "alice", t0) is True
    assert welcome_module._record(db_path, "bob", t0_plus_30m) is False
    with sqlite3.connect(db_path) as conn:
        first_seen = conn.execute(
            "SELECT first_seen FROM seen_clients WHERE id = 'bob'"
        ).fetchone()[0]
    assert first_seen == t0_plus_30m  # recorded immediately despite no greeting
    assert welcome_module._record(db_path, "bob", t0_plus_2h) is False  # never greeted


def test_record_cooldown_runs_from_the_last_newcomer(tmp_path: Path) -> None:
    # The clock is the most recently recorded newcomer, greeted or not:
    # bob (ungreeted, 30 minutes after alice) still pushes carol's arrival
    # 45 minutes later inside the cooldown, while dave — over an hour after
    # carol — is greeted again.
    db_path = tmp_path / "seen.db"
    welcome_module._init_db(db_path)
    assert welcome_module._record(db_path, "alice", "2026-01-01T00:00:00+00:00")
    assert not welcome_module._record(db_path, "bob", "2026-01-01T00:30:00+00:00")
    assert not welcome_module._record(db_path, "carol", "2026-01-01T01:15:00+00:00")
    assert welcome_module._record(db_path, "dave", "2026-01-01T02:20:00+00:00")
