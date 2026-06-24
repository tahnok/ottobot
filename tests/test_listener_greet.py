import pytest

from helpers import ReplyRecorder, dm
from ottobot import MeshBot
from ottobot.commands import greet, register_module


@pytest.fixture
def bot() -> MeshBot:
    bot = MeshBot(name="ottobot")
    register_module(bot, greet)
    return bot


async def test_greets_a_bare_hello(bot: MeshBot, reply: ReplyRecorder) -> None:
    handled = await bot.dispatch(dm("hello"), reply)
    assert handled
    assert reply.replies == ["hi there! send !help to see what I can do."]


async def test_greeting_is_case_and_punctuation_insensitive(
    bot: MeshBot, reply: ReplyRecorder
) -> None:
    assert await bot.dispatch(dm("Hey!"), reply)
    assert reply.replies == ["hi there! send !help to see what I can do."]


async def test_ignores_ordinary_chatter(bot: MeshBot, reply: ReplyRecorder) -> None:
    handled = await bot.dispatch(dm("hello there, how are you"), reply)
    assert not handled
    assert reply.replies == []
