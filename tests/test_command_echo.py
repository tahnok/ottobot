import pytest

from helpers import ReplyRecorder, dm
from ottawa_meshbot import MeshBot
from ottawa_meshbot.commands import echo, register_module


@pytest.fixture
def bot() -> MeshBot:
    bot = MeshBot()
    register_module(bot, echo)
    return bot


async def test_echo_repeats_args(bot: MeshBot, reply: ReplyRecorder) -> None:
    assert await bot.dispatch(dm("!echo hello world"), reply)
    assert reply.replies == ["hello world"]


async def test_echo_without_args(bot: MeshBot, reply: ReplyRecorder) -> None:
    assert await bot.dispatch(dm("!echo"), reply)
    assert reply.replies == ["(nothing to echo)"]
