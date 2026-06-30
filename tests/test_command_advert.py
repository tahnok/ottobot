import pytest

from helpers import ReplyRecorder, dm
from ottobot import DeviceError, MeshBot
from ottobot.commands import advert, register_module


class FakeDevice:
    """Records send_advert calls; optionally fails like a rejecting radio."""

    def __init__(self, fail: bool = False) -> None:
        self.adverts: list[bool] = []
        self.fail = fail

    async def send_advert(self, flood: bool = False) -> None:
        if self.fail:
            raise DeviceError("nope")
        self.adverts.append(flood)


@pytest.fixture
def device() -> FakeDevice:
    return FakeDevice()


@pytest.fixture
def bot(device: FakeDevice) -> MeshBot:
    bot = MeshBot(name="ottobot")
    register_module(bot, advert)
    bot.device = device
    return bot


async def test_advert_defaults_to_zero_hop(
    bot: MeshBot, device: FakeDevice, reply: ReplyRecorder
) -> None:
    await bot.dispatch(dm("!advert"), reply)
    assert device.adverts == [False]
    assert reply.replies == ["Sent zero-hop advert."]


async def test_advert_flood(
    bot: MeshBot, device: FakeDevice, reply: ReplyRecorder
) -> None:
    await bot.dispatch(dm("!advert flood"), reply)
    assert device.adverts == [True]
    assert reply.replies == ["Sent flood advert."]


async def test_advert_flood_is_case_insensitive(
    bot: MeshBot, device: FakeDevice, reply: ReplyRecorder
) -> None:
    await bot.dispatch(dm("!advert FLOOD"), reply)
    assert device.adverts == [True]


async def test_advert_rejects_unknown_argument(
    bot: MeshBot, device: FakeDevice, reply: ReplyRecorder
) -> None:
    await bot.dispatch(dm("!advert wat"), reply)
    assert device.adverts == []
    assert reply.replies == ["Usage: !advert [flood]"]


async def test_advert_without_device(reply: ReplyRecorder) -> None:
    bot = MeshBot(name="ottobot")
    register_module(bot, advert)
    await bot.dispatch(dm("!advert"), reply)
    assert reply.replies == ["No radio connected, can't send an advert."]


async def test_advert_reports_device_error(reply: ReplyRecorder) -> None:
    bot = MeshBot(name="ottobot")
    register_module(bot, advert)
    bot.device = FakeDevice(fail=True)
    await bot.dispatch(dm("!advert"), reply)
    assert reply.replies == ["Couldn't send advert: nope"]
