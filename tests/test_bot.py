import pytest

from helpers import ReplyRecorder, channel_msg, dm
from ottobot import Command, Context, MeshBot, listener


class TestParse:
    def test_command_with_args(self, bot: MeshBot) -> None:
        assert bot.parse("!echo hello world") == ("echo", "hello world")

    def test_command_without_args(self, bot: MeshBot) -> None:
        assert bot.parse("!ping") == ("ping", "")

    def test_surrounding_whitespace(self, bot: MeshBot) -> None:
        assert bot.parse("  !ping  ") == ("ping", "")

    def test_non_command_text(self, bot: MeshBot) -> None:
        assert bot.parse("hello there") is None

    def test_bare_prefix(self, bot: MeshBot) -> None:
        assert bot.parse("!") is None

    def test_custom_prefix(self) -> None:
        bot = MeshBot(name="ottobot", prefix="/")
        assert bot.parse("/ping") == ("ping", "")
        assert bot.parse("!ping") is None


class TestRegistration:
    def test_decorator_registers_command(self, bot: MeshBot) -> None:
        @bot.command("ping", help="pong back")
        async def ping(ctx: Context) -> str:
            return "pong"

        command = bot.registry.get("ping")
        assert command is not None
        assert command.help == "pong back"

    def test_aliases_resolve_to_same_command(self, bot: MeshBot) -> None:
        @bot.command("weather", aliases=("wx",))
        async def weather(ctx: Context) -> str:
            return "sunny"

        assert bot.registry.get("wx") is bot.registry.get("weather")

    def test_lookup_is_case_insensitive(self, bot: MeshBot) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        assert bot.registry.get("PING") is not None

    def test_duplicate_name_rejected(self, bot: MeshBot) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        with pytest.raises(ValueError):

            @bot.command("ping")
            async def ping2(ctx: Context) -> str:
                return "pong2"

    def test_alias_colliding_with_existing_name_rejected(self, bot: MeshBot) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        with pytest.raises(ValueError):
            bot.add_command(Command(name="other", handler=ping, aliases=("ping",)))


class TestDispatch:
    async def test_returned_string_is_sent_as_reply(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        handled = await bot.dispatch(dm("!ping"), reply)
        assert handled
        assert reply.replies == ["pong"]

    async def test_handler_can_reply_directly(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("multi")
        async def multi(ctx: Context) -> None:
            await ctx.reply("one")
            await ctx.reply("two")

        await bot.dispatch(dm("!multi"), reply)
        assert reply.replies == ["one", "two"]

    async def test_args_are_passed_to_handler(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("echo")
        async def echo(ctx: Context) -> str:
            return ctx.args

        await bot.dispatch(dm("!echo hello world"), reply)
        assert reply.replies == ["hello world"]

    async def test_non_command_text_is_ignored(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        handled = await bot.dispatch(dm("just chatting"), reply)
        assert not handled
        assert reply.replies == []

    async def test_unknown_command_is_ignored(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        handled = await bot.dispatch(dm("!nosuchthing"), reply)
        assert not handled
        assert reply.replies == []

    async def test_handler_returning_none_sends_nothing(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("quiet")
        async def quiet(ctx: Context) -> None:
            return None

        handled = await bot.dispatch(dm("!quiet"), reply)
        assert handled
        assert reply.replies == []

    async def test_handler_exception_is_caught_and_reported(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("boom")
        async def boom(ctx: Context) -> str:
            raise RuntimeError("kaboom")

        handled = await bot.dispatch(dm("!boom"), reply)
        assert handled
        assert reply.replies == ["Sorry, !boom hit an error."]

    async def test_channel_messages_handled_when_addressed(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        handled = await bot.dispatch(channel_msg("@[ottobot] !ping"), reply)
        assert handled
        assert reply.replies == ["pong"]

    async def test_channel_messages_ignored_when_disabled(
        self, reply: ReplyRecorder
    ) -> None:
        bot = MeshBot(name="ottobot", respond_in_channels=False)

        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        handled = await bot.dispatch(channel_msg("@[ottobot] !ping"), reply)
        assert not handled
        assert reply.replies == []


class TestListeners:
    async def test_listener_runs_on_non_command_text(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        seen: list[str] = []

        @bot.listener
        async def watch(ctx: Context) -> None:
            seen.append(ctx.message.text)

        handled = await bot.dispatch(dm("just chatting"), reply)
        assert seen == ["just chatting"]
        # Listener observed but did not reply, and it is not a command.
        assert not handled
        assert reply.replies == []

    async def test_listener_can_reply_to_any_message(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.listener
        async def echo_back(ctx: Context) -> str:
            return f"heard: {ctx.message.text}"

        handled = await bot.dispatch(dm("hello"), reply)
        # A listener reply counts as handled even with no command.
        assert handled
        assert reply.replies == ["heard: hello"]

    async def test_listener_can_reply_directly(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.listener
        async def chatty(ctx: Context) -> None:
            await ctx.reply("one")
            await ctx.reply("two")

        await bot.dispatch(dm("hi"), reply)
        assert reply.replies == ["one", "two"]

    async def test_listener_runs_alongside_command(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        @bot.listener
        async def note(ctx: Context) -> str:
            return "noted"

        handled = await bot.dispatch(dm("!ping"), reply)
        assert handled
        # Listeners run first, then the command.
        assert reply.replies == ["noted", "pong"]

    async def test_listeners_run_in_registration_order(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.listener
        async def first(ctx: Context) -> str:
            return "first"

        @bot.listener
        async def second(ctx: Context) -> str:
            return "second"

        await bot.dispatch(dm("hi"), reply)
        assert reply.replies == ["first", "second"]

    async def test_listener_exception_does_not_break_dispatch(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        @bot.listener
        async def boom(ctx: Context) -> str:
            raise RuntimeError("kaboom")

        @bot.listener
        async def survivor(ctx: Context) -> str:
            return "still here"

        handled = await bot.dispatch(dm("!ping"), reply)
        assert handled
        # The failing listener is swallowed; later listeners and the command
        # still run, and no error reply is sent for the listener.
        assert reply.replies == ["still here", "pong"]

    async def test_listener_runs_on_channel_messages(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.listener
        async def watch(ctx: Context) -> str:
            return "saw it"

        # No address needed: listeners see every channel message.
        handled = await bot.dispatch(channel_msg("anything"), reply)
        assert handled
        assert reply.replies == ["saw it"]

    async def test_listener_skipped_when_channels_disabled(
        self, reply: ReplyRecorder
    ) -> None:
        bot = MeshBot(name="ottobot", respond_in_channels=False)

        @bot.listener
        async def watch(ctx: Context) -> str:
            return "saw it"

        handled = await bot.dispatch(channel_msg("anything"), reply)
        assert not handled
        assert reply.replies == []

    def test_bare_listener_decorator_marks_handler(self) -> None:
        bot = MeshBot(name="ottobot")

        @listener
        async def standalone(ctx: Context) -> None:
            return None

        bot.add_listener(standalone)
        assert standalone in bot._listeners


def _named_bot() -> MeshBot:
    bot = MeshBot(name="ottobot")

    @bot.command("ping")
    async def ping(ctx: Context) -> str:
        return "pong"

    @bot.command("status", requires_address=False)
    async def status(ctx: Context) -> str:
        return "ok"

    return bot


class TestAddressing:
    def test_strip_address_app_mention_form(self) -> None:
        # The MeshCore app inserts mentions as "@[Name]".
        bot = MeshBot(name="ottobot")
        assert bot.strip_address("@[ottobot] !ping") == ("!ping", True)
        assert bot.strip_address("@[ottobot]!ping") == ("!ping", True)
        assert bot.strip_address("@[OttoBot] !ping") == ("!ping", True)

    def test_strip_address_with_separators(self) -> None:
        bot = MeshBot(name="ottobot")
        assert bot.strip_address("ottobot !ping") == ("!ping", True)
        assert bot.strip_address("ottobot: !ping") == ("!ping", True)
        assert bot.strip_address("ottobot, !ping") == ("!ping", True)
        assert bot.strip_address("OttoBot !ping") == ("!ping", True)
        assert bot.strip_address("@ottobot !ping") == ("!ping", True)

    def test_strip_address_requires_name_to_stand_alone(self) -> None:
        bot = MeshBot(name="ottobot")
        # "ottobotanist" must not be read as addressing "ottobot".
        assert bot.strip_address("ottobotanist !ping") == ("ottobotanist !ping", False)

    def test_strip_address_when_not_addressed(self) -> None:
        bot = MeshBot(name="ottobot")
        assert bot.strip_address("!ping") == ("!ping", False)

    async def test_dm_needs_no_name(self, reply: ReplyRecorder) -> None:
        bot = _named_bot()
        handled = await bot.dispatch(dm("!ping"), reply)
        assert handled
        assert reply.replies == ["pong"]

    async def test_dm_tolerates_name(self, reply: ReplyRecorder) -> None:
        bot = _named_bot()
        handled = await bot.dispatch(dm("ottobot !ping"), reply)
        assert handled
        assert reply.replies == ["pong"]

    async def test_channel_requires_name(self, reply: ReplyRecorder) -> None:
        bot = _named_bot()
        handled = await bot.dispatch(channel_msg("!ping"), reply)
        assert not handled
        assert reply.replies == []

    async def test_channel_runs_when_addressed(self, reply: ReplyRecorder) -> None:
        bot = _named_bot()
        handled = await bot.dispatch(channel_msg("ottobot !ping"), reply)
        assert handled
        assert reply.replies == ["pong"]

    async def test_channel_runs_with_app_mention(self, reply: ReplyRecorder) -> None:
        bot = _named_bot()
        handled = await bot.dispatch(channel_msg("@[ottobot] !ping"), reply)
        assert handled
        assert reply.replies == ["pong"]

    async def test_channel_opt_out_runs_without_name(
        self, reply: ReplyRecorder
    ) -> None:
        bot = _named_bot()
        handled = await bot.dispatch(channel_msg("!status"), reply)
        assert handled
        assert reply.replies == ["ok"]

    async def test_context_exposes_sender_and_dm_flag(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        seen: dict[str, object] = {}

        @bot.command("who")
        async def who(ctx: Context) -> None:
            seen["sender"] = ctx.sender_name
            seen["is_dm"] = ctx.is_dm

        await bot.dispatch(dm("!who"), reply)
        assert seen == {"sender": "alice", "is_dm": True}


class TestHelp:
    async def test_help_lists_commands_with_descriptions(
        self, bot: MeshBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("ping", help="Check liveness")
        async def ping(ctx: Context) -> str:
            return "pong"

        await bot.dispatch(dm("!help"), reply)
        assert len(reply.replies) == 1
        text = reply.replies[0]
        assert "!help - List available commands" in text
        assert "!ping - Check liveness" in text
