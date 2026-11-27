import pytest

from helpers import ReplyRecorder, addressed
from ottobot import Ottobot
from ottobot.bot import MAX_MESSAGE_LEN
from ottobot.commands import channels, register_module


@pytest.fixture
def bot() -> Ottobot:
    bot = Ottobot(name="ottobot")
    register_module(bot, channels)
    return bot


async def test_channels_lists_all(bot: Ottobot, reply: ReplyRecorder) -> None:
    await bot.dispatch(addressed("!channels"), reply)
    assert reply.replies[0].startswith("Channels: ")
    listed = " ".join(reply.replies).removeprefix("Channels: ").split()
    assert listed == list(channels.CHANNELS)


async def test_channels_each_reply_fits_in_a_packet(
    bot: Ottobot, reply: ReplyRecorder
) -> None:
    await bot.dispatch(addressed("!channels"), reply)
    assert len(reply.replies) > 1
    assert all(len(r) <= MAX_MESSAGE_LEN for r in reply.replies)


async def test_channels_single_reply_when_short(
    bot: Ottobot, reply: ReplyRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(channels, "CHANNELS", ("#ottawa", "#bots"))
    await bot.dispatch(addressed("!channels"), reply)
    assert reply.replies == ["Channels: #ottawa #bots"]
