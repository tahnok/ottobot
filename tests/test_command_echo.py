import pytest

from helpers import ReplyRecorder, addressed
from ottobot import Ottobot
from ottobot.commands import echo, register_module


@pytest.fixture
def bot() -> Ottobot:
    bot = Ottobot(name="ottobot")
    register_module(bot, echo)
    return bot


async def test_echo_repeats_args(bot: Ottobot, reply: ReplyRecorder) -> None:
    await bot.dispatch(addressed("!echo hello world"), reply)
    assert reply.replies == ["hello world"]


async def test_echo_without_args(bot: Ottobot, reply: ReplyRecorder) -> None:
    await bot.dispatch(addressed("!echo"), reply)
    assert reply.replies == ["(nothing to echo)"]
