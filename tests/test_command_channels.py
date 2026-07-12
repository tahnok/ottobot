import pytest

from helpers import ReplyRecorder, addressed
from ottobot import Ottobot
from ottobot.commands import channels, register_module


@pytest.fixture
def bot() -> Ottobot:
    bot = Ottobot(name="ottobot")
    register_module(bot, channels)
    return bot


async def test_channels_lists_all(bot: Ottobot, reply: ReplyRecorder) -> None:
    await bot.dispatch(addressed("!channels"), reply)
    assert reply.replies == [
        "Channels: #ottawa #testing #hike #bike #hamradio "
        "#games #aircraft #watersports #ott-alerts"
    ]


async def test_channels_fits_in_short_message(
    bot: Ottobot, reply: ReplyRecorder
) -> None:
    await bot.dispatch(addressed("!channels"), reply)
    assert len(reply.replies[0]) <= 160
