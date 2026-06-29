"""Tests for the @on_start lifecycle hook: marker, collection, and setup()."""

import types

from ottobot import MeshBot, OnStart, on_start
from ottobot.registry import module_on_start
from ottobot.sinks import register_module


class TestOnStartMarker:
    def test_decorator_returns_handler_unchanged(self) -> None:
        async def hook(bot: MeshBot) -> None: ...

        assert on_start()(hook) is hook

    def test_decorator_attaches_metadata(self) -> None:
        @on_start()
        async def hook(bot: MeshBot) -> None: ...

        meta = getattr(hook, "_meshbot_on_start")
        assert isinstance(meta, OnStart)
        assert meta.handler is hook

    def test_module_on_start_collects_marked_handlers(self) -> None:
        module = types.ModuleType("fake")

        @on_start()
        async def hook(bot: MeshBot) -> None: ...

        hook.__module__ = "fake"
        setattr(module, "hook", hook)
        assert [h.handler for h in module_on_start(module)] == [hook]

    def test_imported_handlers_are_not_collected(self) -> None:
        @on_start()
        async def hook(bot: MeshBot) -> None: ...

        hook.__module__ = "somewhere_else"
        impostor = types.ModuleType("impostor")
        setattr(impostor, "hook", hook)
        assert module_on_start(impostor) == []


class TestSetup:
    async def test_setup_runs_registered_hooks_with_the_bot(self, bot: MeshBot) -> None:
        seen: list[MeshBot] = []

        async def hook(b: MeshBot) -> None:
            seen.append(b)

        bot.add_on_start(OnStart(handler=hook))
        await bot.setup()
        assert seen == [bot]

    async def test_setup_with_no_hooks_is_a_noop(self, bot: MeshBot) -> None:
        await bot.setup()  # should not raise

    def test_register_module_registers_on_start_hooks(self, bot: MeshBot) -> None:
        module = types.ModuleType("fake")

        @on_start()
        async def hook(b: MeshBot) -> None: ...

        hook.__module__ = "fake"
        setattr(module, "hook", hook)

        register_module(bot, module)
        assert [h.handler for h in bot._on_start] == [hook]
