"""Tests for scheduled tasks: the @task marker, registration, and loading.

These cover the task machinery only — the weather_alerts task's own
behavior is tested separately.
"""

import importlib
import types
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import ottobot.tasks as tasks_pkg
from ottobot import Ottobot, ScheduledTask, task, TaskContext
from ottobot.channels import OTT_ALERTS, PUBLIC
from ottobot.cli import build_bot
from ottobot.config import BotConfig
from ottobot.discovery import iter_module_names
from ottobot.registry import module_tasks
from ottobot.tasks import load_tasks, register_module


class TestTaskMarker:
    def test_decorator_returns_handler_unchanged(self) -> None:
        async def handler(ctx: TaskContext) -> None: ...

        decorator = task("noop", interval=timedelta(minutes=1), channel=PUBLIC)
        assert decorator(handler) is handler

    def test_decorator_attaches_task_metadata(self) -> None:
        @task(
            "noop",
            interval=timedelta(minutes=5),
            channel=OTT_ALERTS,
            help="does nothing",
        )
        async def handler(ctx: TaskContext) -> None: ...

        meta = getattr(handler, "_ottobot_task")
        assert isinstance(meta, ScheduledTask)
        assert meta.name == "noop"
        assert meta.handler is handler
        assert meta.interval == timedelta(minutes=5)
        assert meta.channel == OTT_ALERTS
        assert meta.help == "does nothing"

    def test_module_tasks_collects_marked_handlers(self) -> None:
        module = types.ModuleType("fake")

        @task("first", interval=timedelta(minutes=1), channel=PUBLIC)
        async def first(ctx: TaskContext) -> None: ...

        @task("second", interval=timedelta(minutes=1), channel=PUBLIC)
        async def second(ctx: TaskContext) -> None: ...

        # module_tasks only keeps handlers defined in the module itself.
        first.__module__ = "fake"
        second.__module__ = "fake"
        setattr(module, "first", first)
        setattr(module, "second", second)

        tasks = module_tasks(module)
        assert all(isinstance(t, ScheduledTask) for t in tasks)
        assert {t.handler for t in tasks} == {first, second}

    def test_module_tasks_ignores_unmarked_functions(self) -> None:
        module = types.ModuleType("fake")

        async def plain(ctx: TaskContext) -> None: ...

        plain.__module__ = "fake"
        setattr(module, "plain", plain)
        assert module_tasks(module) == []

    def test_imported_handlers_are_not_collected(self) -> None:
        # A handler defined elsewhere but imported into a module must not be
        # picked up, so importing another task's handler can't register it
        # twice.
        @task("noop", interval=timedelta(minutes=1), channel=PUBLIC)
        async def handler(ctx: TaskContext) -> None: ...

        handler.__module__ = "somewhere_else"
        impostor = types.ModuleType("impostor")
        setattr(impostor, "handler", handler)
        assert module_tasks(impostor) == []


OTTAWA = ZoneInfo("America/Toronto")


async def _noop(ctx: TaskContext) -> None: ...


def at_task(*at: time) -> ScheduledTask:
    return ScheduledTask("noop", _noop, None, PUBLIC, at=at)


class TestTaskSchedule:
    def test_needs_interval_or_at(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            ScheduledTask("noop", _noop, None, PUBLIC)

    def test_rejects_both_interval_and_at(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            ScheduledTask(
                "noop", _noop, timedelta(hours=1), PUBLIC, at=(time(9, tzinfo=OTTAWA),)
            )

    def test_at_times_need_a_timezone(self) -> None:
        with pytest.raises(ValueError, match="tzinfo"):
            at_task(time(9))

    def test_next_run_picks_the_next_time_today(self) -> None:
        scheduled = at_task(time(9, tzinfo=OTTAWA), time(17, tzinfo=OTTAWA))
        after = datetime(2026, 11, 3, 12, tzinfo=OTTAWA)
        assert scheduled.next_run(after) == datetime(2026, 11, 3, 17, tzinfo=OTTAWA)

    def test_next_run_wraps_to_tomorrow(self) -> None:
        scheduled = at_task(time(9, tzinfo=OTTAWA), time(17, tzinfo=OTTAWA))
        after = datetime(2026, 11, 3, 18, tzinfo=OTTAWA)
        assert scheduled.next_run(after) == datetime(2026, 11, 4, 9, tzinfo=OTTAWA)

    def test_next_run_is_strictly_after(self) -> None:
        # Asking right at a slot gives the following one, so a task that
        # just ran isn't run again for the same slot.
        scheduled = at_task(time(9, tzinfo=OTTAWA), time(17, tzinfo=OTTAWA))
        after = datetime(2026, 11, 3, 9, tzinfo=OTTAWA)
        assert scheduled.next_run(after) == datetime(2026, 11, 3, 17, tzinfo=OTTAWA)

    def test_next_run_uses_the_times_own_timezone(self) -> None:
        # 9:00 Ottawa is 14:00 UTC in November (EST, UTC-5).
        scheduled = at_task(time(9, tzinfo=OTTAWA))
        after = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        assert scheduled.next_run(after) == datetime(
            2026, 11, 3, 14, tzinfo=timezone.utc
        )

    def test_next_run_keeps_wall_clock_time_across_dst(self) -> None:
        # Clocks fall back on 2026-11-01: 9:00 local moves from 13:00 UTC
        # to 14:00 UTC, but stays 9:00 on the wall.
        scheduled = at_task(time(9, tzinfo=OTTAWA))
        after = datetime(2026, 10, 30, 12, tzinfo=OTTAWA)
        first = scheduled.next_run(after)
        second = scheduled.next_run(first)
        assert first.astimezone(timezone.utc).hour == 13
        assert second.astimezone(timezone.utc).hour == 14
        assert second.astimezone(OTTAWA).hour == 9

    def test_decorator_accepts_at(self) -> None:
        nine = time(9, tzinfo=OTTAWA)

        @task("noop", at=(nine,), channel=PUBLIC)
        async def handler(ctx: TaskContext) -> None: ...

        meta = getattr(handler, "_ottobot_task")
        assert meta.interval is None
        assert meta.at == (nine,)


class TestTaskRegistration:
    def test_add_task_registers_on_bot(self, bot: Ottobot) -> None:
        async def handler(ctx: TaskContext) -> None: ...

        scheduled = ScheduledTask(
            name="noop",
            handler=handler,
            interval=timedelta(minutes=1),
            channel=PUBLIC,
        )
        bot.add_task(scheduled)
        assert bot.tasks == [scheduled]

    def test_bot_task_decorator_registers(self, bot: Ottobot) -> None:
        @bot.task(
            "noop",
            interval=timedelta(minutes=1),
            channel=OTT_ALERTS,
            help="does nothing",
        )
        async def handler(ctx: TaskContext) -> None: ...

        (registered,) = bot.tasks
        assert registered.name == "noop"
        assert registered.handler is handler
        assert registered.channel == OTT_ALERTS
        assert registered.help == "does nothing"


class TestTaskInvocation:
    async def test_handler_runs_with_a_task_context(self) -> None:
        seen: list[str | None] = []

        async def handler(ctx: TaskContext) -> str:
            seen.append(ctx.config.discord_webhook_url)
            return "done"

        replies: list[str] = []

        async def reply(text: str) -> None:
            replies.append(text)

        ctx = TaskContext(
            _reply=reply, config=BotConfig(discord_webhook_url="https://x")
        )
        result = await handler(ctx)
        assert result == "done"
        assert seen == ["https://x"]


class TestTaskLoading:
    def test_register_module_registers_marked_tasks(self, bot: Ottobot) -> None:
        module = types.ModuleType("fake")

        @task("noop", interval=timedelta(minutes=1), channel=PUBLIC)
        async def handler(ctx: TaskContext) -> None: ...

        handler.__module__ = "fake"
        setattr(module, "handler", handler)

        registered = register_module(bot, module)
        assert len(registered) == 1
        assert bot.tasks == registered

    def test_load_tasks_loads_weather_alerts(self) -> None:
        loaded = load_tasks(Ottobot(name="ottobot"))
        assert loaded == iter_module_names(tasks_pkg.__path__)
        assert "weather_alerts" in loaded

    def test_every_task_module_defines_a_task(self) -> None:
        for name in iter_module_names(tasks_pkg.__path__):
            module = importlib.import_module(f"ottobot.tasks.{name}")
            assert module_tasks(
                module
            ), f"{module.__name__} must define at least one @task handler"

    def test_build_bot_exposes_all_tasks(self) -> None:
        bot = build_bot(name="ottobot")
        names = {t.name for t in bot.tasks}
        assert "weather_alerts" in names
