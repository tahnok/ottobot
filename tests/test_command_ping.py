import pytest

from helpers import ReplyRecorder, addressed
from ottobot import MeshBot
from ottobot.commands import ping, register_module
from ottobot.context import IncomingMessage


@pytest.fixture
def bot() -> MeshBot:
    bot = MeshBot(name="ottobot")
    register_module(bot, ping)
    return bot


async def test_ping_without_path_info(bot: MeshBot, reply: ReplyRecorder) -> None:
    await bot.dispatch(addressed("!ping"), reply)
    assert reply.replies == ["@[alice] pong (unknown path)"]


async def test_ping_direct(bot: MeshBot, reply: ReplyRecorder) -> None:
    await bot.dispatch(addressed("!ping", path_len=255), reply)
    assert reply.replies == ["@[alice] pong (direct)"]


async def test_ping_reports_hops(bot: MeshBot, reply: ReplyRecorder) -> None:
    msg = addressed("!ping", path_len=2, path="a1b2", path_hash_mode=0)
    await bot.dispatch(msg, reply)
    assert reply.replies == ["@[alice] pong (2 hops via a1,b2)"]


async def test_ping_without_sender_name(bot: MeshBot, reply: ReplyRecorder) -> None:
    msg = IncomingMessage(text="@[ottobot] !ping", channel_idx=0, path_len=255)
    await bot.dispatch(msg, reply)
    assert reply.replies == ["@[you] pong (direct)"]
