import pytest

from helpers import ReplyRecorder, dm
from ottobot import MeshBot
from ottobot.commands import ping, register_module
from ottobot.context import IncomingMessage


@pytest.fixture
def bot() -> MeshBot:
    bot = MeshBot(name="ottobot")
    register_module(bot, ping)
    return bot


async def test_ping_without_path_info(bot: MeshBot, reply: ReplyRecorder) -> None:
    await bot.dispatch(dm("!ping"), reply)
    assert reply.replies == ["pong alice (unknown path)"]


async def test_ping_direct(bot: MeshBot, reply: ReplyRecorder) -> None:
    await bot.dispatch(dm("!ping", path_len=255), reply)
    assert reply.replies == ["pong alice (direct)"]


async def test_ping_reports_hops(bot: MeshBot, reply: ReplyRecorder) -> None:
    msg = dm("!ping", path_len=2, path="a1b2", path_hash_mode=0)
    await bot.dispatch(msg, reply)
    assert reply.replies == ["pong alice (2 hops via a1,b2)"]


async def test_ping_without_sender_name(bot: MeshBot, reply: ReplyRecorder) -> None:
    msg = IncomingMessage(text="!ping", sender_key="abcd1234", path_len=255)
    await bot.dispatch(msg, reply)
    assert reply.replies == ["pong you (direct)"]
