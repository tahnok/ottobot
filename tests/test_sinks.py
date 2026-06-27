"""Tests for message sinks: the @sink marker, registry, dispatch, and loading.

These cover the sink machinery only — the welcome sink's own behavior is
tested separately.
"""

import types

import pytest

from helpers import ReplyRecorder, channel_msg, dm
from ottobot import Context, MeshBot, Sink, sink
from ottobot.registry import SinkRegistry, module_sinks
from ottobot.sinks import load_sinks, register_module


class TestSinkMarker:
    def test_decorator_returns_handler_unchanged(self) -> None:
        async def handler(ctx: Context) -> None: ...

        assert sink()(handler) is handler

    def test_decorator_attaches_sink_metadata(self) -> None:
        @sink()
        async def handler(ctx: Context) -> None: ...

        meta = getattr(handler, "_meshbot_sink")
        assert isinstance(meta, Sink)
        assert meta.handler is handler

    def test_module_sinks_collects_marked_handlers(self) -> None:
        module = types.ModuleType("fake")

        @sink()
        async def first(ctx: Context) -> None: ...

        @sink()
        async def second(ctx: Context) -> None: ...

        # module_sinks only keeps handlers defined in the module itself.
        first.__module__ = "fake"
        second.__module__ = "fake"
        setattr(module, "first", first)
        setattr(module, "second", second)

        sinks = module_sinks(module)
        assert all(isinstance(s, Sink) for s in sinks)
        assert {s.handler for s in sinks} == {first, second}

    def test_module_sinks_ignores_unmarked_functions(self) -> None:
        module = types.ModuleType("fake")

        async def plain(ctx: Context) -> None: ...

        plain.__module__ = "fake"
        setattr(module, "plain", plain)
        assert module_sinks(module) == []

    def test_imported_handlers_are_not_collected(self) -> None:
        # A handler defined elsewhere but imported into a module must not be
        # picked up, so importing another sink's handler can't register it twice.
        @sink()
        async def handler(ctx: Context) -> None: ...

        handler.__module__ = "somewhere_else"
        impostor = types.ModuleType("impostor")
        setattr(impostor, "handler", handler)
        assert module_sinks(impostor) == []


class TestSinkRegistry:
    def test_empty_registry_has_no_sinks(self) -> None:
        assert SinkRegistry().all() == []

    def test_add_sink_registers_on_bot(self, bot: MeshBot) -> None:
        async def handler(ctx: Context) -> None: ...

        s = Sink(handler=handler)
        bot.add_sink(s)
        assert bot.sink_registry.all() == [s]


class TestSinkDispatch:
    async def test_sink_runs_on_non_command_text(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        seen: list[str] = []

        async def watch(ctx: Context) -> None:
            seen.append(ctx.message.text)

        bot.add_sink(Sink(handler=watch))
        await bot.dispatch(dm("just chatting"), reply)
        assert seen == ["just chatting"]

    async def test_sink_returned_string_is_sent_as_reply(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        async def greet(ctx: Context) -> str:
            return "hello"

        bot.add_sink(Sink(handler=greet))
        await bot.dispatch(dm("hi"), reply)
        assert reply.replies == ["hello"]

    async def test_sink_returning_none_sends_nothing(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        async def quiet(ctx: Context) -> None:
            return None

        bot.add_sink(Sink(handler=quiet))
        await bot.dispatch(dm("hi"), reply)
        assert reply.replies == []

    async def test_all_registered_sinks_run(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        async def one(ctx: Context) -> str:
            return "1"

        async def two(ctx: Context) -> str:
            return "2"

        bot.add_sink(Sink(handler=one))
        bot.add_sink(Sink(handler=two))
        await bot.dispatch(dm("hi"), reply)
        assert set(reply.replies) == {"1", "2"}

    async def test_sink_context_has_no_command_name_and_full_text_args(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        captured: dict[str, object] = {}

        async def cap(ctx: Context) -> None:
            captured["command_name"] = ctx.command_name
            captured["args"] = ctx.args

        bot.add_sink(Sink(handler=cap))
        await bot.dispatch(dm("!ping extra"), reply)
        assert captured == {"command_name": None, "args": "!ping extra"}

    async def test_sink_runs_in_channel_when_responses_disabled(
        self, reply: ReplyRecorder
    ) -> None:
        # Sinks observe every message, even on channels the bot won't answer.
        bot = MeshBot(name="ottobot", respond_in_channels=False)
        seen: list[str] = []

        async def watch(ctx: Context) -> None:
            seen.append(ctx.message.text)

        bot.add_sink(Sink(handler=watch))
        await bot.dispatch(channel_msg("hello channel"), reply)
        assert seen == ["hello channel"]


class TestSinkLoading:
    def test_register_module_registers_marked_sinks(self, bot: MeshBot) -> None:
        module = types.ModuleType("fake")

        @sink()
        async def watch(ctx: Context) -> None: ...

        watch.__module__ = "fake"
        setattr(module, "watch", watch)

        registered = register_module(bot, module)
        assert len(registered) == 1
        assert bot.sink_registry.all() == registered

    def test_module_without_sinks_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ottobot.sinks as sinks_pkg

        monkeypatch.setattr(sinks_pkg, "iter_module_names", lambda: ["broken"])
        monkeypatch.setattr(
            sinks_pkg.importlib,
            "import_module",
            lambda name: types.ModuleType(name),
        )
        with pytest.raises(TypeError, match="must define at least one @sink"):
            load_sinks(MeshBot(name="ottobot"))
