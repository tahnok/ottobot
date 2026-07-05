"""Command registration and lookup."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bot import MeshBot
    from .context import Context, TaskContext

CommandHandler = Callable[["Context"], Awaitable[str | None]]
TaskHandler = Callable[["TaskContext"], Awaitable[str | None]]
# Boot-time hooks get the bot itself, so they can read its config (e.g.
# db_path) and set up whatever they need before the first message arrives.
OnStartHandler = Callable[["MeshBot"], Awaitable[None]]

_COMMAND_ATTR = "_meshbot_command"
_SINK_ATTR = "_meshbot_sink"
_TASK_ATTR = "_meshbot_task"
_ON_START_ATTR = "_meshbot_on_start"


@dataclass
class Command:
    """A named command and the coroutine that handles it.

    requires_address controls whether the bot must be addressed by name
    for this command to run in a *channel* (e.g. "ottobot !ping"). It has
    no effect on direct messages, which are always addressed to the bot.
    Set it False for commands that should answer any channel message
    carrying the prefix, without the name.
    """

    name: str
    handler: CommandHandler
    help: str = ""
    aliases: tuple[str, ...] = ()
    requires_address: bool = True


@dataclass
class Sink:
    """A function that's called on every message the bot receives."""

    handler: CommandHandler


@dataclass
class ScheduledTask:
    """A named handler that's run on a timer instead of in response to a message.

    channel names the config channel the task's output is sent on; the
    runner looks its index up in the config's [[channels]] entries.
    """

    name: str
    handler: TaskHandler
    interval: timedelta
    channel: str
    help: str = ""


@dataclass
class OnStart:
    """A coroutine run once at boot, before the bot handles any messages."""

    handler: OnStartHandler


def command(
    name: str,
    *,
    help: str = "",
    aliases: tuple[str, ...] = (),
    requires_address: bool = True,
) -> Callable[[CommandHandler], CommandHandler]:
    """Mark a module-level coroutine as a command handler.

    This only attaches metadata to the function — no bot instance is
    needed at import time. The command modules under
    ottobot.commands use this; load_commands() later collects the
    marked handlers via module_commands() and registers them on the bot.

    See Command for what requires_address does.
    """

    def decorator(handler: CommandHandler) -> CommandHandler:
        setattr(
            handler,
            _COMMAND_ATTR,
            Command(
                name=name,
                handler=handler,
                help=help,
                aliases=aliases,
                requires_address=requires_address,
            ),
        )
        return handler

    return decorator


def sink() -> Callable[[CommandHandler], CommandHandler]:
    """Mark a module-level coroutine as a message sink.

    This only attaches metadata to the function — no bot instance is
    needed at import time. The sink modules under
    ottobot.sinks use this; load_sinks() later collects the
    marked handlers via module_sinks() and registers them on the bot.
    """

    def decorator(handler: CommandHandler) -> CommandHandler:
        setattr(
            handler,
            _SINK_ATTR,
            Sink(handler=handler),
        )
        return handler

    return decorator


def task(
    name: str, *, interval: timedelta, channel: str, help: str = ""
) -> Callable[[TaskHandler], TaskHandler]:
    """Mark a module-level coroutine as a scheduled task handler.

    This only attaches metadata to the function — no bot instance is
    needed at import time. The task modules under ottobot.tasks use
    this; load_tasks() later collects the marked handlers via
    module_tasks() and registers them on the bot. interval is how often
    the runner calls the handler; channel is the name of the config
    channel the task's output is sent on.
    """

    def decorator(handler: TaskHandler) -> TaskHandler:
        setattr(
            handler,
            _TASK_ATTR,
            ScheduledTask(
                name=name,
                handler=handler,
                interval=interval,
                channel=channel,
                help=help,
            ),
        )
        return handler

    return decorator


def on_start() -> Callable[[OnStartHandler], OnStartHandler]:
    """Mark a module-level coroutine to run once at boot.

    Like @sink, this only attaches metadata at import time. The loaders
    collect marked handlers via module_on_start() and register them on the
    bot; MeshBot.setup() awaits them all before the first message, passing
    the bot so the hook can read its config (e.g. db_path).
    """

    def decorator(handler: OnStartHandler) -> OnStartHandler:
        setattr(handler, _ON_START_ATTR, OnStart(handler=handler))
        return handler

    return decorator


def module_commands(module: ModuleType) -> list[Command]:
    """The @command-marked handlers defined in *module*, in definition order.

    Handlers merely imported into the module (e.g. from a shared helper)
    are excluded, so importing another command's handler can't register
    it twice.
    """
    return [
        cmd
        for obj in vars(module).values()
        if (cmd := getattr(obj, _COMMAND_ATTR, None)) is not None
        and getattr(obj, "__module__", None) == module.__name__
    ]


def module_sinks(module: ModuleType) -> list[Sink]:
    """The @sink-marked handlers defined in *module*, in definition order.

    Handlers merely imported into the module (e.g. from a shared helper)
    are excluded, so importing another sink's handler can't register
    it twice.
    """
    return [
        cmd
        for obj in vars(module).values()
        if (cmd := getattr(obj, _SINK_ATTR, None)) is not None
        and getattr(obj, "__module__", None) == module.__name__
    ]


def module_tasks(module: ModuleType) -> list[ScheduledTask]:
    """The @task-marked handlers defined in *module*, in definition order.

    Handlers merely imported into the module (e.g. from a shared helper)
    are excluded, so importing another task's handler can't register it
    twice.
    """
    return [
        t
        for obj in vars(module).values()
        if (t := getattr(obj, _TASK_ATTR, None)) is not None
        and getattr(obj, "__module__", None) == module.__name__
    ]


def module_on_start(module: ModuleType) -> list[OnStart]:
    """The @on_start-marked handlers defined in *module*, in definition order.

    Handlers merely imported into the module are excluded, mirroring
    module_commands/module_sinks.
    """
    return [
        hook
        for obj in vars(module).values()
        if (hook := getattr(obj, _ON_START_ATTR, None)) is not None
        and getattr(obj, "__module__", None) == module.__name__
    ]


@dataclass
class CommandRegistry:
    """Holds commands and resolves names (including aliases) to them."""

    _commands: dict[str, Command] = field(default_factory=dict)
    _lookup: dict[str, Command] = field(default_factory=dict)

    def register(self, command: Command) -> None:
        for name in (command.name, *command.aliases):
            key = name.lower()
            if key in self._lookup:
                raise ValueError(f"command name {name!r} is already registered")
        self._commands[command.name.lower()] = command
        for name in (command.name, *command.aliases):
            self._lookup[name.lower()] = command

    def get(self, name: str) -> Command | None:
        return self._lookup.get(name.lower())

    def all(self) -> list[Command]:
        """All registered commands, sorted by name (aliases excluded)."""
        return sorted(self._commands.values(), key=lambda c: c.name)


@dataclass
class SinkRegistry:
    """Holds sinks."""

    _sinks: list[Sink] = field(default_factory=list)

    def register(self, sink: Sink) -> None:
        self._sinks.append(sink)

    def all(self) -> list[Sink]:
        """All registered sinks."""
        return self._sinks


@dataclass
class TaskRegistry:
    """Holds scheduled tasks."""

    _tasks: list[ScheduledTask] = field(default_factory=list)

    def register(self, task: ScheduledTask) -> None:
        self._tasks.append(task)

    def all(self) -> list[ScheduledTask]:
        """All registered tasks."""
        return self._tasks
