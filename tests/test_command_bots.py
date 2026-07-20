import pytest

from helpers import ReplyRecorder, addressed, channel_msg
from ottobot import Ottobot
from ottobot.channels import BOTS, OTTOBOT_TESTING, PUBLIC, TESTING
from ottobot.commands import bots, register_module
from ottobot.commands.bots import GREETING


@pytest.fixture
def bot() -> Ottobot:
    bot = Ottobot(name="ottobot")
    register_module(bot, bots)
    return bot


async def test_bots_answers_without_being_addressed(
    bot: Ottobot, reply: ReplyRecorder
) -> None:
    await bot.dispatch(channel_msg("!bots", idx=BOTS.index), reply)
    assert reply.replies == [GREETING]


async def test_bots_answers_when_addressed_too(
    bot: Ottobot, reply: ReplyRecorder
) -> None:
    await bot.dispatch(addressed("!bots"), reply)
    assert reply.replies == [GREETING]


@pytest.mark.parametrize("channel", [TESTING, OTTOBOT_TESTING])
async def test_bots_silent_on_other_command_channels(
    bot: Ottobot, reply: ReplyRecorder, channel
) -> None:
    await bot.dispatch(channel_msg("!bots", idx=channel.index), reply)
    assert reply.replies == []


async def test_bots_silent_on_public_channel(
    bot: Ottobot, reply: ReplyRecorder
) -> None:
    await bot.dispatch(channel_msg("!bots", idx=PUBLIC.index), reply)
    assert reply.replies == []


async def test_greeting_fits_in_one_packet(bot: Ottobot, reply: ReplyRecorder) -> None:
    assert len(GREETING) <= 140
