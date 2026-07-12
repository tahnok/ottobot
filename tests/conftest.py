import pytest

from helpers import ReplyRecorder
from ottobot import OttoBot


@pytest.fixture
def bot() -> OttoBot:
    return OttoBot(name="ottobot")


@pytest.fixture
def reply() -> ReplyRecorder:
    return ReplyRecorder()
