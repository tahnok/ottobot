import pytest

from helpers import ReplyRecorder, dm
from ottobot import MeshBot
from ottobot.commands import register_module, source


@pytest.fixture
def bot() -> MeshBot:
    bot = MeshBot(name="ottobot")
    register_module(bot, source)
    return bot


async def test_source(bot: MeshBot, reply: ReplyRecorder) -> None:
    await bot.dispatch(dm("!source"), reply)
    assert reply.replies == [
        "my source code is available at "
        "https://github.com/tahnok/ottobot and contributions are welcome"
    ]
