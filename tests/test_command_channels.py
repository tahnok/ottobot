import pytest

from helpers import ReplyRecorder, addressed
from ottobot import Ottobot
from ottobot.commands import channels, register_module


@pytest.fixture
def bot() -> Ottobot:
    bot = Ottobot(name="ottobot")
    register_module(bot, channels)
    return bot


async def test_channels_lists_all(bot: Ottobot, reply: ReplyRecorder) -> None:
    await bot.dispatch(addressed("!channels"), reply)
    
    # The expected output matches the exact split of the 19 channels
    expected_reply = (
        "Channels (1/2): #aircraft #aylmer #barrhaven #bike #bots #caf #cars #constancebay #games\n"
        "Channels (2/2): #hamradio #hike #music #ott-alerts #ottawa #queer #radio #testing #wardriving #watersports"
    )
    
    assert reply.replies == [expected_reply]


async def test_channels_fits_in_short_message(
    bot: Ottobot, reply: ReplyRecorder
) -> None:
    await bot.dispatch(addressed("!channels"), reply)
    
    # Split the return string by newline, just like the radio simulator does
    messages = reply.replies[0].split("\n")
    
    # Verify every individual message chunk fits under the limit
    for msg in messages:
        assert len(msg) <= 160