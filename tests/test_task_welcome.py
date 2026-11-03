"""Tests for the welcome scheduled task."""

from datetime import datetime

from ottobot.channels import PUBLIC
from ottobot.config import BotConfig
from ottobot.context import TaskContext
from ottobot.registry import module_tasks
from ottobot.tasks import welcome as welcome_mod
from ottobot.tasks.welcome import OTTAWA, WELCOME


async def _no_reply(text: str) -> None:
    raise AssertionError("the welcome task returns its message, never replies")


def test_task_posts_on_the_public_channel_at_fixed_times() -> None:
    (scheduled,) = module_tasks(welcome_mod)
    assert scheduled.channel is PUBLIC
    assert scheduled.interval is None  # so it never posts at startup
    assert scheduled.next_run(datetime(2026, 11, 3, 10, tzinfo=OTTAWA)) == (
        datetime(2026, 11, 3, 13, tzinfo=OTTAWA)
    )
    assert scheduled.next_run(datetime(2026, 11, 3, 22, tzinfo=OTTAWA)) == (
        datetime(2026, 11, 4, 0, tzinfo=OTTAWA)
    )
    assert scheduled.next_run(datetime(2026, 11, 4, 0, tzinfo=OTTAWA)) == (
        datetime(2026, 11, 4, 9, tzinfo=OTTAWA)
    )


async def test_task_returns_the_welcome() -> None:
    ctx = TaskContext(_reply=_no_reply, config=BotConfig())
    assert await welcome_mod.welcome(ctx) == WELCOME


def test_welcome_message_fits_in_a_single_packet() -> None:
    # Mesh bandwidth is limited; keep the greeting to a single packet.
    assert len(WELCOME.encode("utf-8")) < 140
