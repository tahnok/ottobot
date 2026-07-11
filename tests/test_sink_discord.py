"""Tests for the discord webhook sink."""

import pytest

from helpers import ReplyRecorder, channel_msg, dm
from ottobot import MeshBot
from ottobot.config import BotConfig
from ottobot.sinks import discord as discord_mod
from ottobot.sinks import register_module

WEBHOOK = "https://discord.com/api/webhooks/1/abc"


def make_bot(config: BotConfig) -> MeshBot:
    """A bot with only the discord sink loaded."""
    bot = MeshBot(name="ottobot", config=config)
    register_module(bot, discord_mod)
    return bot


@pytest.fixture
def posts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    """Capture post_to_discord calls instead of hitting the network."""
    captured: list[tuple[str, dict]] = []

    async def fake_post(url: str, payload: dict) -> None:
        captured.append((url, payload))

    monkeypatch.setattr(discord_mod, "post_to_discord", fake_post)
    return captured


async def test_public_channel_message_is_posted(
    posts: list[tuple[str, dict]], reply: ReplyRecorder
) -> None:
    bot = make_bot(BotConfig(discord_webhook_url=WEBHOOK))
    await bot.dispatch(channel_msg("hello mesh"), reply)
    assert posts == [(WEBHOOK, {"username": "alice", "content": "[public] hello mesh"})]
    assert reply.replies == []


async def test_command_messages_are_also_posted(
    posts: list[tuple[str, dict]], reply: ReplyRecorder
) -> None:
    # Sinks see every message, including command invocations.
    bot = make_bot(BotConfig(discord_webhook_url=WEBHOOK))
    await bot.dispatch(channel_msg("!ping"), reply)
    assert posts == [(WEBHOOK, {"username": "alice", "content": "[public] !ping"})]


async def test_dm_is_not_posted(
    posts: list[tuple[str, dict]], reply: ReplyRecorder
) -> None:
    bot = make_bot(BotConfig(discord_webhook_url=WEBHOOK))
    await bot.dispatch(dm("psst"), reply)
    assert posts == []


async def test_non_public_channel_is_not_posted(
    posts: list[tuple[str, dict]], reply: ReplyRecorder
) -> None:
    bot = make_bot(BotConfig(discord_webhook_url=WEBHOOK))
    await bot.dispatch(channel_msg("secret", idx=1), reply)
    assert posts == []


async def test_no_webhook_configured_does_not_post(
    posts: list[tuple[str, dict]], reply: ReplyRecorder
) -> None:
    bot = make_bot(BotConfig())
    await bot.dispatch(channel_msg("hello"), reply)
    assert posts == []


async def test_empty_message_is_not_posted(
    posts: list[tuple[str, dict]], reply: ReplyRecorder
) -> None:
    bot = make_bot(BotConfig(discord_webhook_url=WEBHOOK))
    await bot.dispatch(channel_msg("   "), reply)
    assert posts == []


async def test_post_failure_does_not_break_dispatch(
    monkeypatch: pytest.MonkeyPatch, reply: ReplyRecorder
) -> None:
    async def boom(url: str, payload: dict) -> None:
        raise RuntimeError("discord is down")

    monkeypatch.setattr(discord_mod, "post_to_discord", boom)
    bot = make_bot(BotConfig(discord_webhook_url=WEBHOOK))
    # Must not raise, and must not emit a reply onto the mesh.
    await bot.dispatch(channel_msg("hello"), reply)
    assert reply.replies == []
