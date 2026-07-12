"""Tests for the @on_start lifecycle hook: marker, collection, and setup()."""

import types

from ottobot import Ottobot, OnStart, on_start
from ottobot.registry import module_on_start
from ottobot.sinks import register_module


class TestOnStartMarker:
    def test_decorator_returns_handler_unchanged(self) -> None:
        async def hook(bot: Ottobot) -> None: ...

        assert on_start()(hook) is hook

    def test_decorator_attaches_metadata(self) -> None:
        @on_start()
        async def hook(bot: Ottobot) -> None: ...

        meta = getattr(hook, "_ottobot_on_start")
        assert isinstance(meta, OnStart)
        assert meta.handler is hook

    def test_module_on_start_collects_marked_handlers(self) -> None:
        module = types.ModuleType("fake")

        @on_start()
        async def hook(bot: Ottobot) -> None: ...

        hook.__module__ = "fake"
        setattr(module, "hook", hook)
        assert [h.handler for h in module_on_start(module)] == [hook]

    def test_imported_handlers_are_not_collected(self) -> None:
        @on_start()
        async def hook(bot: Ottobot) -> None: ...

        hook.__module__ = "somewhere_else"
        impostor = types.ModuleType("impostor")
        setattr(impostor, "hook", hook)
        assert module_on_start(impostor) == []


class TestSetup:
    async def test_setup_runs_registered_hooks_with_the_bot(self, bot: Ottobot) -> None:
        seen: list[Ottobot] = []

        async def hook(b: Ottobot) -> None:
            seen.append(b)

        bot.add_on_start(OnStart(handler=hook))
        await bot.setup()
        assert seen == [bot]

    async def test_setup_with_no_hooks_is_a_noop(self, bot: Ottobot) -> None:
        await bot.setup()  # should not raise

    def test_register_module_registers_on_start_hooks(self, bot: Ottobot) -> None:
        module = types.ModuleType("fake")

        @on_start()
        async def hook(b: Ottobot) -> None: ...

        hook.__module__ = "fake"
        setattr(module, "hook", hook)

        register_module(bot, module)
        assert [h.handler for h in bot._on_start] == [hook]
