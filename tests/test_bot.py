import pytest

from helpers import ReplyRecorder, addressed, channel_msg
from ottobot import Command, Context, OttoBot


class TestParse:
    def test_command_with_args(self, bot: OttoBot) -> None:
        assert bot.parse("!echo hello world") == ("echo", "hello world")

    def test_command_without_args(self, bot: OttoBot) -> None:
        assert bot.parse("!ping") == ("ping", "")

    def test_surrounding_whitespace(self, bot: OttoBot) -> None:
        assert bot.parse("  !ping  ") == ("ping", "")

    def test_non_command_text(self, bot: OttoBot) -> None:
        assert bot.parse("hello there") is None

    def test_bare_prefix(self, bot: OttoBot) -> None:
        assert bot.parse("!") is None

    def test_custom_prefix(self) -> None:
        bot = OttoBot(name="ottobot", prefix="/")
        assert bot.parse("/ping") == ("ping", "")
        assert bot.parse("!ping") is None


class TestRegistration:
    def test_decorator_registers_command(self, bot: OttoBot) -> None:
        @bot.command("ping", help="pong back")
        async def ping(ctx: Context) -> str:
            return "pong"

        command = bot.registry.get("ping")
        assert command is not None
        assert command.help == "pong back"

    def test_aliases_resolve_to_same_command(self, bot: OttoBot) -> None:
        @bot.command("weather", aliases=("wx",))
        async def weather(ctx: Context) -> str:
            return "sunny"

        assert bot.registry.get("wx") is bot.registry.get("weather")

    def test_lookup_is_case_insensitive(self, bot: OttoBot) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        assert bot.registry.get("PING") is not None

    def test_duplicate_name_rejected(self, bot: OttoBot) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        with pytest.raises(ValueError):

            @bot.command("ping")
            async def ping2(ctx: Context) -> str:
                return "pong2"

    def test_alias_colliding_with_existing_name_rejected(self, bot: OttoBot) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        with pytest.raises(ValueError):
            bot.add_command(Command(name="other", handler=ping, aliases=("ping",)))


class TestDispatch:
    async def test_returned_string_is_sent_as_reply(
        self, bot: OttoBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        await bot.dispatch(addressed("!ping"), reply)
        assert reply.replies == ["pong"]

    async def test_handler_can_reply_directly(
        self, bot: OttoBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("multi")
        async def multi(ctx: Context) -> None:
            await ctx.reply("one")
            await ctx.reply("two")

        await bot.dispatch(addressed("!multi"), reply)
        assert reply.replies == ["one", "two"]

    async def test_args_are_passed_to_handler(
        self, bot: OttoBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("echo")
        async def echo(ctx: Context) -> str:
            return ctx.args

        await bot.dispatch(addressed("!echo hello world"), reply)
        assert reply.replies == ["hello world"]

    async def test_non_command_text_is_ignored(
        self, bot: OttoBot, reply: ReplyRecorder
    ) -> None:
        await bot.dispatch(channel_msg("just chatting"), reply)
        assert reply.replies == []

    async def test_unknown_command_is_ignored(
        self, bot: OttoBot, reply: ReplyRecorder
    ) -> None:
        await bot.dispatch(addressed("!nosuchthing"), reply)
        assert reply.replies == []

    async def test_handler_returning_none_sends_nothing(
        self, bot: OttoBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("quiet")
        async def quiet(ctx: Context) -> None:
            return None

        await bot.dispatch(addressed("!quiet"), reply)
        assert reply.replies == []

    async def test_handler_exception_is_caught_and_reported(
        self, bot: OttoBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("boom")
        async def boom(ctx: Context) -> str:
            raise RuntimeError("kaboom")

        await bot.dispatch(addressed("!boom"), reply)
        assert reply.replies == ["Sorry, !boom hit an error."]

    async def test_channel_messages_handled_when_addressed(
        self, bot: OttoBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        await bot.dispatch(channel_msg("@[ottobot] !ping"), reply)
        assert reply.replies == ["pong"]


def _named_bot() -> OttoBot:
    bot = OttoBot(name="ottobot")

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
        bot = OttoBot(name="ottobot")
        assert bot.strip_address("@[ottobot] !ping") == ("!ping", True)
        assert bot.strip_address("@[ottobot]!ping") == ("!ping", True)
        assert bot.strip_address("@[OttoBot] !ping") == ("!ping", True)

    def test_strip_address_with_separators(self) -> None:
        bot = OttoBot(name="ottobot")
        assert bot.strip_address("ottobot !ping") == ("!ping", True)
        assert bot.strip_address("ottobot: !ping") == ("!ping", True)
        assert bot.strip_address("ottobot, !ping") == ("!ping", True)
        assert bot.strip_address("OttoBot !ping") == ("!ping", True)
        assert bot.strip_address("@ottobot !ping") == ("!ping", True)

    def test_strip_address_requires_name_to_stand_alone(self) -> None:
        bot = OttoBot(name="ottobot")
        # "ottobotanist" must not be read as addressing "ottobot".
        assert bot.strip_address("ottobotanist !ping") == ("ottobotanist !ping", False)

    def test_strip_address_when_not_addressed(self) -> None:
        bot = OttoBot(name="ottobot")
        assert bot.strip_address("!ping") == ("!ping", False)

    async def test_channel_requires_name(self, reply: ReplyRecorder) -> None:
        bot = _named_bot()
        await bot.dispatch(channel_msg("!ping"), reply)
        assert reply.replies == []

    async def test_channel_runs_when_addressed(self, reply: ReplyRecorder) -> None:
        bot = _named_bot()
        await bot.dispatch(channel_msg("ottobot !ping"), reply)
        assert reply.replies == ["pong"]

    async def test_channel_runs_with_app_mention(self, reply: ReplyRecorder) -> None:
        bot = _named_bot()
        await bot.dispatch(channel_msg("@[ottobot] !ping"), reply)
        assert reply.replies == ["pong"]

    async def test_channel_opt_out_runs_without_name(
        self, reply: ReplyRecorder
    ) -> None:
        bot = _named_bot()
        await bot.dispatch(channel_msg("!status"), reply)
        assert reply.replies == ["ok"]

    async def test_context_exposes_sender(
        self, bot: OttoBot, reply: ReplyRecorder
    ) -> None:
        seen: dict[str, object] = {}

        @bot.command("who")
        async def who(ctx: Context) -> None:
            seen["sender"] = ctx.sender_name

        await bot.dispatch(addressed("!who"), reply)
        assert seen == {"sender": "alice"}


class TestHelp:
    async def test_help_lists_commands_with_descriptions(
        self, bot: OttoBot, reply: ReplyRecorder
    ) -> None:
        @bot.command("ping", help="Check liveness")
        async def ping(ctx: Context) -> str:
            return "pong"

        await bot.dispatch(addressed("!help"), reply)
        assert len(reply.replies) == 1
        text = reply.replies[0]
        assert "!help - List available commands" in text
        assert "!ping - Check liveness" in text
