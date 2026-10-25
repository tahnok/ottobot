import os

import pytest
from hypothesis import settings

from helpers import ReplyRecorder
from ottobot import Ottobot
from ottobot import runner as runner_module

# Hypothesis profiles, picked with HYPOTHESIS_PROFILE (CI sets "ci"). Shared
# CI runners are slow and noisy, so drop the per-example deadline there
# rather than fail on timing, and print a reproduction blob on failure.
settings.register_profile("ci", deadline=None, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


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
