import pytest

from helpers import ReplyRecorder
from ottobot import Ottobot
from ottobot import runner as runner_module


@pytest.fixture(autouse=True)
def _no_send_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real transmissions are delayed, spaced apart, and followed by a wait
    # for a repeat; drop all of that to zero so the suite doesn't wait
    # seconds per send. Tests that exercise a wait opt back in.
    monkeypatch.setattr(runner_module, "SEND_SPACING_SECONDS", 0.0)
    monkeypatch.setattr(runner_module, "REPLY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(runner_module, "REPLY_JITTER_SECONDS", 0.0)
    monkeypatch.setattr(runner_module, "REPEAT_TIMEOUT_SECONDS", 0.0)


@pytest.fixture
def bot() -> Ottobot:
    return Ottobot(name="ottobot")


@pytest.fixture
def reply() -> ReplyRecorder:
    return ReplyRecorder()
