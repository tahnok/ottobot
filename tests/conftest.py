import pytest

from helpers import ReplyRecorder
from ottobot import Ottobot


@pytest.fixture
def bot() -> Ottobot:
    return Ottobot(name="ottobot")


@pytest.fixture
def reply() -> ReplyRecorder:
    return ReplyRecorder()
