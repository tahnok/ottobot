"""Tests for the welcome scheduled task."""

from datetime import timedelta

import pytest

from ottobot.channels import PUBLIC
from ottobot.config import BotConfig
from ottobot.context import TaskContext
from ottobot.registry import module_tasks
from ottobot.tasks import welcome as welcome_mod
from ottobot.tasks.welcome import WELCOME


async def _no_reply(text: str) -> None:
    raise AssertionError("the welcome task returns its message, never replies")


def test_task_posts_on_the_public_channel_four_times_a_day() -> None:
    (scheduled,) = module_tasks(welcome_mod)
    assert scheduled.channel is PUBLIC
    assert scheduled.interval == timedelta(hours=6)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(welcome_mod, "_started", False)


async def test_startup_call_is_skipped_then_every_call_posts() -> None:
    # The runner calls the task right at startup; that call stays quiet so a
    # restart doesn't add an extra welcome. Every later call posts it.
    ctx = TaskContext(_reply=_no_reply, config=BotConfig())
    assert await welcome_mod.welcome(ctx) is None
    assert await welcome_mod.welcome(ctx) == WELCOME
    assert await welcome_mod.welcome(ctx) == WELCOME


def test_welcome_message_fits_in_a_single_packet() -> None:
    # Mesh bandwidth is limited; keep the greeting to a single packet.
    assert len(WELCOME.encode("utf-8")) < 140
