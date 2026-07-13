import pytest

from helpers import ReplyRecorder, addressed, channel_msg
from ottobot import Command, Context, Ottobot, Sink
from ottobot.channels import BOTS, OTT_ALERTS, OTTOBOT_TESTING, PUBLIC, TESTING


class TestParse:
    def test_command_with_args(self, bot: Ottobot) -> None:
        assert bot.parse("!echo hello world") == ("echo", "hello world")

    def test_command_without_args(self, bot: Ottobot) -> None:
        assert bot.parse("!ping") == ("ping", "")

    def test_surrounding_whitespace(self, bot: Ottobot) -> None:
        assert bot.parse("  !ping  ") == ("ping", "")

    def test_non_command_text(self, bot: Ottobot) -> None:
        assert bot.parse("hello there") is None

    def test_bare_prefix(self, bot: Ottobot) -> None:
        assert bot.parse("!") is None

    def test_custom_prefix(self) -> None:
        bot = Ottobot(name="ottobot", prefix="/")
        assert bot.parse("/ping") == ("ping", "")
        assert bot.parse("!ping") is None


class TestRegistration:
    def test_decorator_registers_command(self, bot: Ottobot) -> None:
        @bot.command("ping", help="pong back")
        async def ping(ctx: Context) -> str:
            return "pong"

        command = bot.registry.get("ping")
        assert command is not None
        assert command.help == "pong back"

    def test_aliases_resolve_to_same_command(self, bot: Ottobot) -> None:
        @bot.command("weather", aliases=("wx",))
        async def weather(ctx: Context) -> str:
            return "sunny"

        assert bot.registry.get("wx") is bot.registry.get("weather")

    def test_lookup_is_case_insensitive(self, bot: Ottobot) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        assert bot.registry.get("PING") is not None

    def test_duplicate_name_rejected(self, bot: Ottobot) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        with pytest.raises(ValueError):

            @bot.command("ping")
            async def ping2(ctx: Context) -> str:
                return "pong2"

    def test_alias_colliding_with_existing_name_rejected(self, bot: Ottobot) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        with pytest.raises(ValueError):
            bot.add_command(Command(name="other", handler=ping, aliases=("ping",)))


class TestDispatch:
    async def test_returned_string_is_sent_as_reply(
        self, bot: Ottobot, reply: ReplyRecorder
    ) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        await bot.dispatch(addressed("!ping"), reply)
        assert reply.replies == ["pong"]

    async def test_handler_can_reply_directly(
        self, bot: Ottobot, reply: ReplyRecorder
    ) -> None:
        @bot.command("multi")
        async def multi(ctx: Context) -> None:
            await ctx.reply("one")
            await ctx.reply("two")

        await bot.dispatch(addressed("!multi"), reply)
        assert reply.replies == ["one", "two"]

    async def test_args_are_passed_to_handler(
        self, bot: Ottobot, reply: ReplyRecorder
    ) -> None:
        @bot.command("echo")
        async def echo(ctx: Context) -> str:
            return ctx.args

        await bot.dispatch(addressed("!echo hello world"), reply)
        assert reply.replies == ["hello world"]

    async def test_non_command_text_is_ignored(
        self, bot: Ottobot, reply: ReplyRecorder
    ) -> None:
        await bot.dispatch(channel_msg("just chatting"), reply)
        assert reply.replies == []

    async def test_unknown_command_is_ignored(
        self, bot: Ottobot, reply: ReplyRecorder
    ) -> None:
        await bot.dispatch(addressed("!nosuchthing"), reply)
        assert reply.replies == []

    async def test_handler_returning_none_sends_nothing(
        self, bot: Ottobot, reply: ReplyRecorder
    ) -> None:
        @bot.command("quiet")
        async def quiet(ctx: Context) -> None:
            return None

        await bot.dispatch(addressed("!quiet"), reply)
        assert reply.replies == []

    async def test_handler_exception_is_caught_and_reported(
        self, bot: Ottobot, reply: ReplyRecorder
    ) -> None:
        @bot.command("boom")
        async def boom(ctx: Context) -> str:
            raise RuntimeError("kaboom")

        await bot.dispatch(addressed("!boom"), reply)
        assert reply.replies == ["Sorry, !boom hit an error."]

    async def test_channel_messages_handled_when_addressed(
        self, bot: Ottobot, reply: ReplyRecorder
    ) -> None:
        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        await bot.dispatch(channel_msg("@[ottobot] !ping", idx=BOTS.index), reply)
        assert reply.replies == ["pong"]


def _named_bot() -> Ottobot:
    bot = Ottobot(name="ottobot")

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
        bot = Ottobot(name="ottobot")
        assert bot.strip_address("@[ottobot] !ping") == ("!ping", True)
        assert bot.strip_address("@[ottobot]!ping") == ("!ping", True)
        assert bot.strip_address("@[Ottobot] !ping") == ("!ping", True)

    def test_strip_address_with_separators(self) -> None:
        bot = Ottobot(name="ottobot")
        assert bot.strip_address("ottobot !ping") == ("!ping", True)
        assert bot.strip_address("ottobot: !ping") == ("!ping", True)
        assert bot.strip_address("ottobot, !ping") == ("!ping", True)
        assert bot.strip_address("Ottobot !ping") == ("!ping", True)
        assert bot.strip_address("@ottobot !ping") == ("!ping", True)

    def test_strip_address_requires_name_to_stand_alone(self) -> None:
        bot = Ottobot(name="ottobot")
        # "ottobotanist" must not be read as addressing "ottobot".
        assert bot.strip_address("ottobotanist !ping") == ("ottobotanist !ping", False)

    def test_strip_address_when_not_addressed(self) -> None:
        bot = Ottobot(name="ottobot")
        assert bot.strip_address("!ping") == ("!ping", False)

    async def test_channel_requires_name(self, reply: ReplyRecorder) -> None:
        bot = _named_bot()
        await bot.dispatch(channel_msg("!ping", idx=BOTS.index), reply)
        assert reply.replies == []

    async def test_channel_runs_when_addressed(self, reply: ReplyRecorder) -> None:
        bot = _named_bot()
        await bot.dispatch(channel_msg("ottobot !ping", idx=BOTS.index), reply)
        assert reply.replies == ["pong"]

    async def test_channel_runs_with_app_mention(self, reply: ReplyRecorder) -> None:
        bot = _named_bot()
        await bot.dispatch(channel_msg("@[ottobot] !ping", idx=BOTS.index), reply)
        assert reply.replies == ["pong"]

    async def test_channel_opt_out_runs_without_name(
        self, reply: ReplyRecorder
    ) -> None:
        bot = _named_bot()
        await bot.dispatch(channel_msg("!status", idx=BOTS.index), reply)
        assert reply.replies == ["ok"]

    async def test_context_exposes_sender(
        self, bot: Ottobot, reply: ReplyRecorder
    ) -> None:
        seen: dict[str, object] = {}

        @bot.command("who")
        async def who(ctx: Context) -> None:
            seen["sender"] = ctx.sender_name

        await bot.dispatch(addressed("!who"), reply)
        assert seen == {"sender": "alice"}


class TestCommandChannels:
    """Commands are only answered on the designated command channels."""

    @pytest.mark.parametrize(
        "channel", [BOTS, TESTING, OTTOBOT_TESTING], ids=lambda c: c.name
    )
    async def test_commands_answer_on_command_channels(
        self, channel, reply: ReplyRecorder
    ) -> None:
        bot = _named_bot()
        await bot.dispatch(channel_msg("@[ottobot] !ping", idx=channel.index), reply)
        assert reply.replies == ["pong"]

    @pytest.mark.parametrize("channel", [PUBLIC, OTT_ALERTS], ids=lambda c: c.name)
    async def test_commands_ignored_elsewhere(
        self, channel, reply: ReplyRecorder
    ) -> None:
        bot = _named_bot()
        await bot.dispatch(channel_msg("@[ottobot] !ping", idx=channel.index), reply)
        assert reply.replies == []

    async def test_restriction_applies_without_address_requirement(
        self, reply: ReplyRecorder
    ) -> None:
        # Even a requires_address=False command stays quiet off the
        # command channels.
        bot = _named_bot()
        await bot.dispatch(channel_msg("!status", idx=PUBLIC.index), reply)
        assert reply.replies == []

    async def test_sinks_still_see_other_channels(self, reply: ReplyRecorder) -> None:
        bot = _named_bot()
        seen: list[str] = []

        async def watcher(ctx: Context) -> None:
            seen.append(ctx.args)

        bot.add_sink(Sink(handler=watcher))
        await bot.dispatch(channel_msg("hello public", idx=PUBLIC.index), reply)
        assert seen == ["hello public"]

    async def test_command_channels_can_be_overridden(
        self, reply: ReplyRecorder
    ) -> None:
        bot = Ottobot(name="ottobot", command_channels=(PUBLIC,))

        @bot.command("ping")
        async def ping(ctx: Context) -> str:
            return "pong"

        await bot.dispatch(channel_msg("@[ottobot] !ping", idx=PUBLIC.index), reply)
        assert reply.replies == ["pong"]


class TestHelp:
    async def test_help_lists_commands_with_descriptions(
        self, bot: Ottobot, reply: ReplyRecorder
    ) -> None:
        @bot.command("ping", help="Check liveness")
        async def ping(ctx: Context) -> str:
            return "pong"

        await bot.dispatch(addressed("!help"), reply)
        assert len(reply.replies) == 1
        text = reply.replies[0]
        assert "!help - List available commands" in text
        assert "!ping - Check liveness" in text

    async def test_help_chunks_a_long_listing(
        self, bot: Ottobot, reply: ReplyRecorder
    ) -> None:
        # Enough commands that the listing exceeds one mesh packet; it should
        # be split across replies rather than truncated into one.
        from ottobot.bot import MAX_MESSAGE_LEN

        for i in range(30):

            @bot.command(f"cmd{i}", help="does a thing worth describing")
            async def handler(ctx: Context) -> str:
                return "ok"

        await bot.dispatch(addressed("!help"), reply)
        assert len(reply.replies) > 1
        assert all(len(chunk) <= MAX_MESSAGE_LEN for chunk in reply.replies)
        joined = "\n".join(reply.replies)
        assert "!cmd0 - does a thing worth describing" in joined
        assert "!cmd29 - does a thing worth describing" in joined
