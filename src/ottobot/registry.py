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
from datetime import timedelta
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bot import Ottobot
    from .channels import ChannelConfig
    from .context import Context, TaskContext

CommandHandler = Callable[["Context"], Awaitable[str | None]]
TaskHandler = Callable[["TaskContext"], Awaitable[str | None]]
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
    """

    name: str
    handler: CommandHandler
    help: str = ""
    aliases: tuple[str, ...] = ()
    requires_address: bool = True


@dataclass
class ScheduledTask:
    """A named handler that's run on a timer instead of in response to a message.

    interval is how often the runner calls the handler. channel is the
    channel the task's output is broadcast on, referenced directly as one
    of the ``ottobot.channels`` constants (e.g. ``OTT_ALERTS``).
    """

    name: str
    handler: TaskHandler
    interval: timedelta
    channel: "ChannelConfig"
    help: str = ""


def command(
    name: str,
    *,
    help: str = "",
    aliases: tuple[str, ...] = (),
    requires_address: bool = True,
) -> Callable[[CommandHandler], CommandHandler]:
    """Mark a module-level coroutine as a command handler."""

    def decorator(handler: CommandHandler) -> CommandHandler:
        setattr(
            handler,
            _COMMAND_ATTR,
            Command(name, handler, help, aliases, requires_address),
        )
        return handler

    return decorator


def task(
    name: str, *, interval: timedelta, channel: "ChannelConfig", help: str = ""
) -> Callable[[TaskHandler], TaskHandler]:
    """Mark a module-level coroutine as a scheduled task handler."""

    def decorator(handler: TaskHandler) -> TaskHandler:
        setattr(
            handler, _TASK_ATTR, ScheduledTask(name, handler, interval, channel, help)
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
