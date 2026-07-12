from datetime import timedelta

import pytest

from ottobot import Context, Ottobot, TaskContext
from ottobot.channels import PUBLIC
from ottobot.cli import parse_args
from ottobot.simulator import Simulator


@pytest.fixture
def sim(bot: Ottobot) -> Simulator:
    @bot.command("ping")
    async def ping(ctx: Context) -> str:
        return f"pong ({ctx.path_description})"

    @bot.command("whoami")
    async def whoami(ctx: Context) -> str:
        return f"{ctx.sender_name} via channel {ctx.message.channel_idx}"

    return Simulator(bot)


class TestMessages:
    async def test_reply_is_printed_with_bot_prefix(self, sim: Simulator) -> None:
        assert await sim.handle_line("@[ottobot] !ping") == ["bot> pong (direct)"]

    async def test_multiline_reply_is_indented(self, sim: Simulator) -> None:
        @sim.bot.command("lines")
        async def lines(ctx: Context) -> str:
            return "one\ntwo"

        assert await sim.handle_line("@[ottobot] !lines") == ["bot> one", "     two"]

    async def test_unknown_command_explains_silence(self, sim: Simulator) -> None:
        (notice,) = await sim.handle_line("!nosuchthing")
        assert "no response" in notice

    async def test_non_command_text_explains_silence(self, sim: Simulator) -> None:
        (notice,) = await sim.handle_line("just chatting")
        assert "no response" in notice

    async def test_blank_line_prints_nothing(self, sim: Simulator) -> None:
        assert await sim.handle_line("   ") == []

    async def test_defaults_to_channel_zero(self, sim: Simulator) -> None:
        message = sim.build_message("!ping")
        assert message.channel_idx == 0


class TestControls:
    async def test_channel_switch_reaches_handler(self, sim: Simulator) -> None:
        await sim.handle_line("/channel 2")
        assert await sim.handle_line("@[ottobot] !whoami") == ["bot> you via channel 2"]

    async def test_channel_defaults_to_zero(self, sim: Simulator) -> None:
        await sim.handle_line("/channel")
        assert sim.channel_idx == 0

    async def test_name_changes_sender(self, sim: Simulator) -> None:
        await sim.handle_line("/name alice")
        assert await sim.handle_line("@[ottobot] !whoami") == [
            "bot> alice via channel 0"
        ]

    async def test_hops_with_route_shows_in_path(self, sim: Simulator) -> None:
        await sim.handle_line("/hops 2 a1,b2")
        assert await sim.handle_line("@[ottobot] !ping") == [
            "bot> pong (2 hops via a1,b2)"
        ]

    async def test_hops_zero_means_direct(self, sim: Simulator) -> None:
        await sim.handle_line("/hops 3")
        await sim.handle_line("/hops 0")
        assert await sim.handle_line("@[ottobot] !ping") == ["bot> pong (direct)"]

    async def test_hops_rejects_non_hex_route(self, sim: Simulator) -> None:
        (notice,) = await sim.handle_line("/hops 1 zz")
        assert "hex" in notice
        assert sim.path is None

    async def test_status_reports_persona(self, sim: Simulator) -> None:
        await sim.handle_line("/name alice")
        await sim.handle_line("/channel 1")
        (status,) = await sim.handle_line("/status")
        assert "alice" in status
        assert "channel 1" in status
        assert "direct" in status

    async def test_quit_marks_done(self, sim: Simulator) -> None:
        assert not sim.done
        await sim.handle_line("/quit")
        assert sim.done

    async def test_unknown_control_suggests_help(self, sim: Simulator) -> None:
        (notice,) = await sim.handle_line("/bogus")
        assert "/help" in notice

    async def test_prompt_tracks_persona(self, sim: Simulator) -> None:
        assert sim.prompt == "you@ch0> "
        await sim.handle_line("/name alice")
        await sim.handle_line("/channel 2")
        assert sim.prompt == "alice@ch2> "


class TestTaskControl:
    async def test_runs_named_task_and_prints_replies(self, sim: Simulator) -> None:
        @sim.bot.task("greet", interval=timedelta(hours=1), channel=PUBLIC)
        async def greet(ctx: TaskContext) -> str:
            return "hello mesh"

        assert await sim.handle_line("/task greet") == ["task> hello mesh"]

    async def test_task_using_ctx_reply_for_multiple_lines(
        self, sim: Simulator
    ) -> None:
        @sim.bot.task("multi", interval=timedelta(hours=1), channel=PUBLIC)
        async def multi(ctx: TaskContext) -> None:
            await ctx.reply("one")
            await ctx.reply("two")

        assert await sim.handle_line("/task multi") == ["task> one", "task> two"]

    async def test_task_with_no_output(self, sim: Simulator) -> None:
        @sim.bot.task("quiet", interval=timedelta(hours=1), channel=PUBLIC)
        async def quiet(ctx: TaskContext) -> None:
            return None

        assert await sim.handle_line("/task quiet") == ["task 'quiet' ran, no output"]

    async def test_unknown_task_name(self, sim: Simulator) -> None:
        assert await sim.handle_line("/task nosuchtask") == [
            "no such task 'nosuchtask'"
        ]

    async def test_no_name_lists_available_tasks(self, sim: Simulator) -> None:
        @sim.bot.task("greet", interval=timedelta(hours=1), channel=PUBLIC)
        async def greet(ctx: TaskContext) -> str:
            return "hi"

        (notice,) = await sim.handle_line("/task")
        assert "usage" in notice
        assert "greet" in notice


def test_cli_accepts_simulate_flag() -> None:
    args = parse_args(["--simulate"])
    assert args.simulate
