import pytest

from helpers import ReplyRecorder, addressed
from ottobot import OttoBot
from ottobot.commands import register_module, source


@pytest.fixture
def bot() -> OttoBot:
    bot = OttoBot(name="ottobot")
    register_module(bot, source)
    return bot


async def test_source(bot: OttoBot, reply: ReplyRecorder) -> None:
    await bot.dispatch(addressed("!source"), reply)
    assert reply.replies == [
        "my source code is available at "
        "https://github.com/tahnok/ottobot and contributions are welcome"
    ]
