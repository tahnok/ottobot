import pytest

from helpers import ReplyRecorder, addressed
from ottobot import Ottobot
from ottobot.channels import BOTS
from ottobot.commands import ping, register_module
from ottobot.context import IncomingMessage


@pytest.fixture
def bot() -> Ottobot:
    bot = Ottobot(name="ottobot")
    register_module(bot, ping)
    return bot


async def test_ping_without_path_info(bot: Ottobot, reply: ReplyRecorder) -> None:
    await bot.dispatch(addressed("!ping"), reply)
    assert reply.replies == ["@[alice] pong (unknown path)"]


async def test_ping_direct(bot: Ottobot, reply: ReplyRecorder) -> None:
    await bot.dispatch(addressed("!ping", path_len=255), reply)
    assert reply.replies == ["@[alice] pong (direct)"]


async def test_ping_reports_hops(bot: Ottobot, reply: ReplyRecorder) -> None:
    msg = addressed("!ping", path_len=2, path="a1b2", path_hash_mode=0)
    await bot.dispatch(msg, reply)
    assert reply.replies == ["@[alice] pong (2 hops via a1,b2)"]


async def test_ping_without_sender_name(bot: Ottobot, reply: ReplyRecorder) -> None:
    msg = IncomingMessage(text="@[ottobot] !ping", channel_idx=BOTS.index, path_len=255)
    await bot.dispatch(msg, reply)
    assert reply.replies == ["@[you] pong (direct)"]


async def test_ping_links_to_beacon(bot: Ottobot, reply: ReplyRecorder) -> None:
    msg = addressed("!ping", path_len=255, packet_hash="90d5b457307ec193")
    await bot.dispatch(msg, reply)
    assert reply.replies == [
        "@[alice] pong (direct) " "https://m.tahnok.ca/90d5b457307ec193"
    ]


async def test_ping_drops_long_route_to_fit_link(
    bot: Ottobot, reply: ReplyRecorder
) -> None:
    path = "".join(f"{i:04x}" for i in range(20))
    msg = addressed(
        "!ping",
        path_len=20,
        path=path,
        path_hash_mode=1,
        packet_hash="90d5b457307ec193",
    )
    await bot.dispatch(msg, reply)
    assert reply.replies == [
        "@[alice] pong (20 hops) " "https://m.tahnok.ca/90d5b457307ec193"
    ]
