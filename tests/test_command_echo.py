import pytest

from helpers import ReplyRecorder, dm
from ottobot import MeshBot
from ottobot.commands import echo, register_module


@pytest.fixture
def bot() -> MeshBot:
    bot = MeshBot(name="ottobot")
    register_module(bot, echo)
    return bot


async def test_echo_repeats_args(bot: MeshBot, reply: ReplyRecorder) -> None:
    await bot.dispatch(dm("!echo hello world"), reply)
    assert reply.replies == ["hello world"]


async def test_echo_without_args(bot: MeshBot, reply: ReplyRecorder) -> None:
    await bot.dispatch(dm("!echo"), reply)
    assert reply.replies == ["(nothing to echo)"]
