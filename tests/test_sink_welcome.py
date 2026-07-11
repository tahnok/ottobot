"""Tests for the welcome sink: greets each channel name once, persisted."""

import sqlite3
from pathlib import Path

from helpers import ReplyRecorder, channel_msg, dm
from ottobot import IncomingMessage, MeshBot
from ottobot.config import BotConfig, ChannelConfig
from ottobot.sinks import register_module
from ottobot.sinks import welcome as welcome_module
from ottobot.sinks.welcome import WELCOME


async def make_welcome_bot(
    db_path: Path, channels: tuple[ChannelConfig, ...] = ()
) -> MeshBot:
    """A bot with only the welcome sink loaded and its table created."""
    config = BotConfig(database=db_path, channels=channels)
    bot = MeshBot(name="ottobot", config=config)
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


async def test_different_names_each_welcomed(
    tmp_path: Path, reply: ReplyRecorder
) -> None:
    bot = await make_welcome_bot(tmp_path / "seen.db")
    await bot.dispatch(chan("hi", "alice"), reply)
    await bot.dispatch(chan("hi", "bob"), reply)
    assert reply.replies == [WELCOME, WELCOME]


async def test_dms_are_never_welcomed(tmp_path: Path, reply: ReplyRecorder) -> None:
    bot = await make_welcome_bot(tmp_path / "seen.db")
    await bot.dispatch(dm("hello"), reply)  # a brand-new DM sender
    assert reply.replies == []


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
    # With no channels configured, public_channel_idx() defaults to 0, so a
    # message on any other index must not be welcomed.
    bot = await make_welcome_bot(tmp_path / "seen.db")
    await bot.dispatch(chan("hi", "alice", idx=1), reply)
    assert reply.replies == []


async def test_welcomes_only_on_the_configured_public_channel(
    tmp_path: Path, reply: ReplyRecorder
) -> None:
    # "public" pinned to a non-zero index: only that channel greets, and the
    # default idx 0 no longer does.
    channels = (
        ChannelConfig(index=0, name="general"),
        ChannelConfig(index=2, name="public"),
    )
    bot = await make_welcome_bot(tmp_path / "seen.db", channels=channels)
    await bot.dispatch(chan("hi", "alice", idx=0), reply)
    assert reply.replies == []  # idx 0 is no longer the public channel
    await bot.dispatch(chan("hi", "alice", idx=2), reply)
    assert reply.replies == [WELCOME]  # idx 2 is the public channel


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
