"""Tests for per-handler state: the state= argument of @command / @task.

The bot builds each stateful handler's state once, when it registers the
handler, and passes the same object as ctx.state on every call. State lives
on the bot, not the shared module-level marker, so bots don't share it.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

import pytest

from helpers import FakeMeshCore, ReplyRecorder, addressed
from ottobot import Context, Ottobot, TaskContext, command, sink, task
from ottobot.channels import PUBLIC, ChannelConfig
from ottobot.config import BotConfig
from ottobot.registry import Command, ScheduledTask
from ottobot.runner import MeshCoreRunner
from ottobot.simulator import Simulator


@dataclass
class Counter:
    count: int = 0
    seen: list[str] = field(default_factory=list)


@command("count", state=Counter)
async def count(ctx: Context[Counter]) -> str:
    ctx.state.count += 1
    ctx.state.seen.append(ctx.args)
    return str(ctx.state.count)


@task("tick", interval=timedelta(hours=1), channel=PUBLIC, state=Counter)
async def tick(ctx: TaskContext[Counter]) -> str:
    ctx.state.count += 1
    return f"tick {ctx.state.count}"


@pytest.fixture
def mc() -> FakeMeshCore:
    return FakeMeshCore()


def marker[T](handler: object, attr: str, kind: type[T]) -> T:
    meta = getattr(handler, attr)
    assert isinstance(meta, kind)
    return meta


def command_bot() -> Ottobot:
    bot = Ottobot(name="ottobot")
    bot.add_command(marker(count, "_ottobot_command", Command))
    return bot


def task_bot() -> Ottobot:
    bot = Ottobot(name="ottobot", config=BotConfig(channels=(PUBLIC,)))
    bot.add_task(marker(tick, "_ottobot_task", ScheduledTask))
    return bot


class TestMarkers:
    def test_command_marker_keeps_the_factory(self) -> None:
        assert marker(count, "_ottobot_command", Command).state is Counter

    def test_task_marker_keeps_the_factory(self) -> None:
        assert marker(tick, "_ottobot_task", ScheduledTask).state is Counter

    def test_state_defaults_to_none(self) -> None:
        @command("plain")
        async def plain(ctx: Context) -> None: ...

        @task("plain", interval=timedelta(hours=1), channel=PUBLIC)
        async def plain_task(ctx: TaskContext) -> None: ...

        assert marker(plain, "_ottobot_command", Command).state is None
        assert marker(plain_task, "_ottobot_task", ScheduledTask).state is None


class TestCommandState:
    async def test_state_persists_across_calls(self) -> None:
        bot = command_bot()
        reply = ReplyRecorder()
        await bot.dispatch(addressed("!count a"), reply)
        await bot.dispatch(addressed("!count b"), reply)
        assert reply.replies == ["1", "2"]
        assert bot.command_state("count") == Counter(2, ["a", "b"])

    async def test_bots_do_not_share_state(self) -> None:
        first, second = command_bot(), command_bot()
        await first.dispatch(addressed("!count"), ReplyRecorder())
        reply = ReplyRecorder()
        await second.dispatch(addressed("!count"), reply)
        assert reply.replies == ["1"]
        assert first.command_state("count") is not second.command_state("count")

    async def test_aliases_share_the_commands_state(self) -> None:
        bot = Ottobot(name="ottobot")

        @bot.command("hits", aliases=("h",), state=Counter)
        async def hits(ctx: Context[Counter]) -> str:
            ctx.state.count += 1
            return str(ctx.state.count)

        reply = ReplyRecorder()
        await bot.dispatch(addressed("!hits"), reply)
        await bot.dispatch(addressed("!h"), reply)
        assert reply.replies == ["1", "2"]

    async def test_command_without_state_gets_none(self) -> None:
        bot = Ottobot(name="ottobot")

        @bot.command("plain")
        async def plain(ctx: Context) -> str:
            return repr(ctx.state)

        reply = ReplyRecorder()
        await bot.dispatch(addressed("!plain"), reply)
        assert reply.replies == ["None"]
        assert bot.command_state("plain") is None

    async def test_sinks_get_no_state(self) -> None:
        bot = command_bot()
        states: list[object] = []

        @sink()
        async def watcher(ctx: Context) -> None:
            states.append(ctx.state)

        bot.add_sink(watcher)
        await bot.dispatch(addressed("!count"), ReplyRecorder())
        assert states == [None]


class TestTaskState:
    def test_state_is_built_at_registration(self) -> None:
        assert task_bot().task_state("tick") == Counter()

    def test_unknown_task_has_no_state(self) -> None:
        assert task_bot().task_state("nosuchtask") is None

    def test_bot_task_decorator_accepts_state(self) -> None:
        bot = Ottobot(name="ottobot")

        @bot.task("t", interval=timedelta(hours=1), channel=PUBLIC, state=Counter)
        async def t(ctx: TaskContext[Counter]) -> None: ...

        assert bot.task_state("t") == Counter()

    async def test_runner_passes_the_same_state_every_run(
        self, mc: FakeMeshCore
    ) -> None:
        bot = task_bot()
        runner = MeshCoreRunner(bot, mc)
        (scheduled,) = bot.tasks
        await runner._run_task_once(scheduled)
        await runner._run_task_once(scheduled)
        assert bot.task_state("tick") == Counter(2)

    async def test_runner_schedule_keeps_state(self, mc: FakeMeshCore) -> None:
        channel = ChannelConfig(index=0, name="#news")
        bot = Ottobot(name="ottobot", config=BotConfig(channels=(channel,)))

        @bot.task("t", interval=timedelta(hours=1), channel=channel, state=Counter)
        async def t(ctx: TaskContext[Counter]) -> str:
            ctx.state.count += 1
            return f"run {ctx.state.count}"

        runner = MeshCoreRunner(bot, mc)
        await runner.start()
        await asyncio.sleep(0.05)
        await runner.stop()
        assert mc.commands.sent_chan_msgs == [(0, "run 1")]
        assert bot.task_state("t") == Counter(1)

    async def test_simulator_keeps_state_between_task_runs(self) -> None:
        sim = Simulator(task_bot())
        assert await sim.handle_line("/task tick") == ["task> tick 1"]
        assert await sim.handle_line("/task tick") == ["task> tick 2"]
