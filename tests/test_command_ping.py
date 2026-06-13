import pytest

from helpers import ReplyRecorder, dm
from ottawa_meshbot import MeshBot
from ottawa_meshbot.commands import ping, register_module


@pytest.fixture
def bot() -> MeshBot:
    bot = MeshBot()
    register_module(bot, ping)
    return bot


async def test_ping_without_path_info(bot: MeshBot, reply: ReplyRecorder) -> None:
    assert await bot.dispatch(dm("!ping"), reply)
    assert reply.replies == ["pong (unknown path)"]


async def test_ping_direct(bot: MeshBot, reply: ReplyRecorder) -> None:
    assert await bot.dispatch(dm("!ping", path_len=255), reply)
    assert reply.replies == ["pong (direct)"]


async def test_ping_reports_hops(bot: MeshBot, reply: ReplyRecorder) -> None:
    msg = dm("!ping", path_len=2, path="a1b2", path_hash_mode=0)
    assert await bot.dispatch(msg, reply)
    assert reply.replies == ["pong (2 hops via a1,b2)"]


async def test_ping_includes_snr_when_present(
    bot: MeshBot, reply: ReplyRecorder
) -> None:
    msg = dm("!ping", path_len=255, raw={"SNR": 8.5})
    assert await bot.dispatch(msg, reply)
    assert reply.replies == ["pong (direct) SNR 8.5dB"]
