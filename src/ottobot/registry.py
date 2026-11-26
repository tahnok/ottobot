"""Handler markers: the @command/@sink/@task/@on_start decorators.

These only attach metadata to module-level coroutines — no bot instance is
needed at import time. The modules under ottobot.commands / ottobot.sinks /
ottobot.tasks use them; the loaders (see ottobot.discovery) later collect
the marked handlers via the module_*() functions and register them on the
bot.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bot import Ottobot
    from .channels import ChannelConfig
    from .context import Context, TaskContext

# The handler types as stored by the framework: whatever state type a
# handler declares, it's held here as Context[Any] / TaskContext[Any].
CommandHandler = Callable[["Context[Any]"], Awaitable[str | None]]
TaskHandler = Callable[["TaskContext[Any]"], Awaitable[str | None]]
# A zero-argument callable (usually a dataclass) that builds a handler's
# initial state; see the state= argument of @command / @task.
StateFactory = Callable[[], Any]
OnStartHandler = Callable[["Ottobot"], Awaitable[None]]

_COMMAND_ATTR = "_ottobot_command"
_SINK_ATTR = "_ottobot_sink"
_TASK_ATTR = "_ottobot_task"
_ON_START_ATTR = "_ottobot_on_start"


@dataclass
class Command:
    """A named command and the coroutine that handles it.

    requires_address controls whether the bot must be addressed by name
    for this command to run (e.g. "ottobot !ping"). Set it False for
    commands that should answer any channel message carrying the prefix,
    without the name.

    state, when set, is called once per bot to build the object the
    handler sees as ctx.state on every call (see @command).
    """

    name: str
    handler: CommandHandler
    help: str = ""
    aliases: tuple[str, ...] = ()
    requires_address: bool = True
    state: StateFactory | None = None


@dataclass
class ScheduledTask:
    """A named handler that's run on a timer instead of in response to a message.

    A task is scheduled in exactly one of two ways. With interval, the
    runner calls the handler right at startup and then every interval.
    With at, a tuple of timezone-aware clock times (e.g.
    ``time(9, tzinfo=ZoneInfo("America/Toronto"))``), it calls the handler
    every day at each of those times, and never at startup. channel is the
    channel the task's output is broadcast on, referenced directly as one
    of the ``ottobot.channels`` constants (e.g. ``OTT_ALERTS``).

    state, when set, is called once per bot to build the object the
    handler sees as ctx.state on every run (see @task).
    """

    name: str
    handler: TaskHandler
    interval: timedelta | None
    channel: "ChannelConfig"
    help: str = ""
    at: tuple[time, ...] = ()
    state: StateFactory | None = None

    def __post_init__(self) -> None:
        if (self.interval is None) == (not self.at):
            raise ValueError(
                f"task {self.name!r} needs exactly one of interval= or at="
            )
        for t in self.at:
            if t.tzinfo is None:
                raise ValueError(
                    f"task {self.name!r}: at= time {t} needs a tzinfo, "
                    "so it doesn't depend on the host's timezone"
                )

    def next_run(self, after: datetime) -> datetime:
        """The first of the ``at`` times strictly after the aware datetime *after*.

        Each time is wall-clock time in its own timezone, so a 9:00 slot
        stays at 9:00 local across daylight saving changes.
        """
        candidates = []
        for t in self.at:
            tz = t.tzinfo
            local_day = after.astimezone(tz).date()
            for days in (0, 1):
                candidate = datetime.combine(
                    local_day + timedelta(days=days), t.replace(tzinfo=None), tz
                )
                if candidate > after:
                    candidates.append(candidate)
                    break
        return min(candidates)


def command[S = None](
    name: str,
    *,
    help: str = "",
    aliases: tuple[str, ...] = (),
    requires_address: bool = True,
    state: Callable[[], S] | None = None,
) -> Callable[
    [Callable[["Context[S]"], Awaitable[str | None]]],
    Callable[["Context[S]"], Awaitable[str | None]],
]:
    """Mark a module-level coroutine as a command handler.

    Pass state= a zero-argument factory (typically a dataclass) for a
    handler that needs to remember things between calls: each bot calls
    it once at registration, and the handler gets the result as ctx.state
    on every call. Mutate it in place; annotate the handler's argument as
    ``Context[YourState]`` so the type checker knows its fields.
    """

    def decorator(handler: CommandHandler) -> CommandHandler:
        setattr(
            handler,
            _COMMAND_ATTR,
            Command(name, handler, help, aliases, requires_address, state),
        )
        return handler

    return decorator


def task[S = None](
    name: str,
    *,
    channel: "ChannelConfig",
    interval: timedelta | None = None,
    at: tuple[time, ...] = (),
    help: str = "",
    state: Callable[[], S] | None = None,
) -> Callable[
    [Callable[["TaskContext[S]"], Awaitable[str | None]]],
    Callable[["TaskContext[S]"], Awaitable[str | None]],
]:
    """Mark a module-level coroutine as a scheduled task handler.

    Give exactly one of interval= or at= (see ScheduledTask). state= works
    as for @command: the handler gets the object it builds as ctx.state,
    one per bot, kept across runs — use it rather than module globals.
    """

    def decorator(handler: TaskHandler) -> TaskHandler:
        setattr(
            handler,
            _TASK_ATTR,
            ScheduledTask(name, handler, interval, channel, help, at, state),
        )
        return handler

    return decorator


def sink() -> Callable[[CommandHandler], CommandHandler]:
    """Mark a module-level coroutine as a message sink (sees every message)."""

    def decorator(handler: CommandHandler) -> CommandHandler:
        setattr(handler, _SINK_ATTR, handler)
        return handler

    return decorator


def on_start() -> Callable[[OnStartHandler], OnStartHandler]:
    """Mark a module-level coroutine to run once at boot.

    Ottobot.setup() awaits every registered hook before the first message,
    passing the bot so the hook can read its config (e.g. database path).
    """

    def decorator(handler: OnStartHandler) -> OnStartHandler:
        setattr(handler, _ON_START_ATTR, handler)
        return handler

    return decorator


def _marked(module: ModuleType, attr: str) -> list:
    """The *attr* markers on objects defined in *module*, in definition order.

    Handlers merely imported into the module (e.g. from a shared helper)
    are excluded, so importing another module's handler can't register it
    twice.
    """
    return [
        marker
        for obj in vars(module).values()
        if (marker := getattr(obj, attr, None)) is not None
        and getattr(obj, "__module__", None) == module.__name__
    ]


def module_commands(module: ModuleType) -> list[Command]:
    """The @command-marked handlers defined in *module*, as Command objects."""
    return _marked(module, _COMMAND_ATTR)


def module_sinks(module: ModuleType) -> list[CommandHandler]:
    """The @sink-marked handlers defined in *module*."""
    return _marked(module, _SINK_ATTR)


def module_tasks(module: ModuleType) -> list[ScheduledTask]:
    """The @task-marked handlers defined in *module*, as ScheduledTask objects."""
    return _marked(module, _TASK_ATTR)


def module_on_start(module: ModuleType) -> list[OnStartHandler]:
    """The @on_start-marked handlers defined in *module*."""
    return _marked(module, _ON_START_ATTR)
