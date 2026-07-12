"""Tests for scheduled tasks: the @task marker, registry, and loading.

These cover the task machinery only — the weather_alerts task's own
behavior is tested separately.
"""

import importlib
import types
from datetime import timedelta

import pytest

from ottobot import OttoBot, ScheduledTask, task, TaskContext
from ottobot.channels import OTT_ALERTS, PUBLIC
from ottobot.cli import build_bot
from ottobot.registry import TaskRegistry, module_tasks
from ottobot.tasks import iter_module_names, load_tasks, register_module


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


class TestTaskRegistry:
    def test_empty_registry_has_no_tasks(self) -> None:
        assert TaskRegistry().all() == []

    def test_add_task_registers_on_bot(self, bot: OttoBot) -> None:
        async def handler(ctx: TaskContext) -> None: ...

        scheduled = ScheduledTask(
            name="noop",
            handler=handler,
            interval=timedelta(minutes=1),
            channel=PUBLIC,
        )
        bot.add_task(scheduled)
        assert bot.task_registry.all() == [scheduled]

    def test_bot_task_decorator_registers(self, bot: OttoBot) -> None:
        @bot.task(
            "noop",
            interval=timedelta(minutes=1),
            channel=OTT_ALERTS,
            help="does nothing",
        )
        async def handler(ctx: TaskContext) -> None: ...

        (registered,) = bot.task_registry.all()
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

        from ottobot.config import BotConfig

        ctx = TaskContext(
            _reply=reply, config=BotConfig(discord_webhook_url="https://x")
        )
        result = await handler(ctx)
        assert result == "done"
        assert seen == ["https://x"]


class TestTaskLoading:
    def test_register_module_registers_marked_tasks(self, bot: OttoBot) -> None:
        module = types.ModuleType("fake")

        @task("noop", interval=timedelta(minutes=1), channel=PUBLIC)
        async def handler(ctx: TaskContext) -> None: ...

        handler.__module__ = "fake"
        setattr(module, "handler", handler)

        registered = register_module(bot, module)
        assert len(registered) == 1
        assert bot.task_registry.all() == registered

    def test_module_without_tasks_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ottobot.tasks as tasks_pkg

        monkeypatch.setattr(tasks_pkg, "iter_module_names", lambda: ["broken"])
        monkeypatch.setattr(
            tasks_pkg.importlib,
            "import_module",
            lambda name: types.ModuleType(name),
        )
        with pytest.raises(TypeError, match="must define at least one @task"):
            load_tasks(OttoBot(name="ottobot"))

    def test_load_tasks_loads_weather_alerts(self) -> None:
        loaded = load_tasks(OttoBot(name="ottobot"))
        assert loaded == iter_module_names()
        assert "weather_alerts" in loaded

    def test_every_task_module_defines_a_task(self) -> None:
        for name in iter_module_names():
            module = importlib.import_module(f"ottobot.tasks.{name}")
            assert module_tasks(
                module
            ), f"{module.__name__} must define at least one @task handler"

    def test_build_bot_exposes_all_tasks(self) -> None:
        bot = build_bot(name="ottobot")
        names = {t.name for t in bot.task_registry.all()}
        assert "weather_alerts" in names
