import re

import pytest

from helpers import ReplyRecorder, dm
from ottawa_meshbot import MeshBot
from ottawa_meshbot.commands import register_module, roll


@pytest.fixture
def bot() -> MeshBot:
    bot = MeshBot()
    register_module(bot, roll)
    return bot


async def test_roll_with_sides(bot: MeshBot, reply: ReplyRecorder) -> None:
    assert await bot.dispatch(dm("!roll 20"), reply)
    match = re.fullmatch(r"alice rolled a (\d+) \(d20\)", reply.replies[0])
    assert match
    assert 1 <= int(match.group(1)) <= 20


async def test_roll_defaults_to_d6(bot: MeshBot, reply: ReplyRecorder) -> None:
    assert await bot.dispatch(dm("!roll"), reply)
    match = re.fullmatch(r"alice rolled a (\d+) \(d6\)", reply.replies[0])
    assert match
    assert 1 <= int(match.group(1)) <= 6


async def test_roll_rejects_non_numeric_sides(
    bot: MeshBot, reply: ReplyRecorder
) -> None:
    assert await bot.dispatch(dm("!roll banana"), reply)
    assert reply.replies == ["Usage: !roll [sides]"]


async def test_roll_rejects_too_few_sides(bot: MeshBot, reply: ReplyRecorder) -> None:
    assert await bot.dispatch(dm("!roll 1"), reply)
    assert reply.replies == ["A die needs at least 2 sides."]


async def test_dice_alias(bot: MeshBot, reply: ReplyRecorder) -> None:
    assert await bot.dispatch(dm("!dice 6"), reply)
    assert "(d6)" in reply.replies[0]
