"""Handler markers, registration, and lookup.

Commands, sinks, scheduled tasks, and on-start hooks all work the same
way: a decorator (@command, @sink, @task, @on_start) attaches a marker
dataclass to a module-level coroutine at import time — no bot instance
needed — and the loaders (see ottobot.loader) later collect the marked
handlers via the module_*() helpers and register them on the bot. Only
the marker dataclasses differ; the machinery is shared.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from types import ModuleType
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from .bot import Ottobot
    from .channels import ChannelConfig
    from .context import Context, TaskContext

CommandHandler = Callable[["Context"], Awaitable[str | None]]
TaskHandler = Callable[["TaskContext"], Awaitable[str | None]]
OnStartHandler = Callable[["Ottobot"], Awaitable[None]]

# All decorators attach their markers to this one attribute, as a list so
# a handler can carry several markers (e.g. both @sink and @on_start).
_MARKERS_ATTR = "_ottobot_markers"


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
class Sink:
    """A function that's called on every message the bot receives."""

    handler: CommandHandler


@dataclass
class ScheduledTask:
    """A named handler that's run on a timer instead of in response to a message.

    channel is the channel the task's output is broadcast on, referenced
    directly as one of the ``ottobot.channels`` constants (e.g. ``OTT_ALERTS``).
    """

    name: str
    handler: TaskHandler
    interval: timedelta
    channel: "ChannelConfig"
    help: str = ""


@dataclass
class OnStart:
    """A coroutine run once at boot, before the bot handles any messages."""

    handler: OnStartHandler


Marker = Command | Sink | ScheduledTask | OnStart

H = TypeVar("H", bound=Callable[..., object])
M = TypeVar("M", bound=Marker)
T = TypeVar("T")


def _marking(factory: Callable[[H], Marker]) -> Callable[[H], H]:
    """The shared decorator body: attach the marker *factory* builds.

    The handler is returned unchanged; the marker is appended to its
    marker list so the module_*() collectors below can find it later.
    """

    def decorator(handler: H) -> H:
        markers = getattr(handler, _MARKERS_ATTR, None)
        if markers is None:
            markers = []
            setattr(handler, _MARKERS_ATTR, markers)
        markers.append(factory(handler))
        return handler

    return decorator


def handler_markers(handler: Callable[..., object]) -> list[Marker]:
    """The markers the decorators attached to *handler*, oldest first."""
    return list(getattr(handler, _MARKERS_ATTR, ()))


def command(
    name: str,
    *,
    help: str = "",
    aliases: tuple[str, ...] = (),
    requires_address: bool = True,
) -> Callable[[CommandHandler], CommandHandler]:
    """Mark a module-level coroutine as a command handler.

    The command modules under ottobot.commands use this; load_commands()
    later registers the marked handlers on the bot. See Command for what
    requires_address does.
    """
    return _marking(
        lambda handler: Command(
            name=name,
            handler=handler,
            help=help,
            aliases=aliases,
            requires_address=requires_address,
        )
    )


def sink() -> Callable[[CommandHandler], CommandHandler]:
    """Mark a module-level coroutine as a message sink.

    The sink modules under ottobot.sinks use this; load_sinks() later
    registers the marked handlers on the bot.
    """
    return _marking(lambda handler: Sink(handler=handler))


def task(
    name: str, *, interval: timedelta, channel: "ChannelConfig", help: str = ""
) -> Callable[[TaskHandler], TaskHandler]:
    """Mark a module-level coroutine as a scheduled task handler.

    The task modules under ottobot.tasks use this; load_tasks() later
    registers the marked handlers on the bot. interval is how often the
    runner calls the handler; channel is the ottobot.channels constant
    the task's output is broadcast on.
    """
    return _marking(
        lambda handler: ScheduledTask(
            name=name, handler=handler, interval=interval, channel=channel, help=help
        )
    )


def on_start() -> Callable[[OnStartHandler], OnStartHandler]:
    """Mark a module-level coroutine to run once at boot.

    The loaders register marked handlers on the bot; Ottobot.setup()
    awaits them all before the first message, passing the bot so the hook
    can read its config (e.g. db_path).
    """
    return _marking(lambda handler: OnStart(handler=handler))


def module_handlers(module: ModuleType) -> list[Marker]:
    """All markers attached to handlers defined in *module*, in definition order.

    Handlers merely imported into the module (e.g. from a shared helper)
    are excluded, so importing another module's handler can't register it
    twice.
    """
    return [
        marker
        for obj in vars(module).values()
        if getattr(obj, "__module__", None) == module.__name__
        for marker in handler_markers(obj)
    ]


def module_markers(module: ModuleType, kind: type[M]) -> list[M]:
    """The *kind* markers attached to handlers defined in *module*."""
    return [marker for marker in module_handlers(module) if isinstance(marker, kind)]


def module_commands(module: ModuleType) -> list[Command]:
    """The @command markers defined in *module*, in definition order."""
    return module_markers(module, Command)


def module_sinks(module: ModuleType) -> list[Sink]:
    """The @sink markers defined in *module*, in definition order."""
    return module_markers(module, Sink)


def module_tasks(module: ModuleType) -> list[ScheduledTask]:
    """The @task markers defined in *module*, in definition order."""
    return module_markers(module, ScheduledTask)


def module_on_start(module: ModuleType) -> list[OnStart]:
    """The @on_start markers defined in *module*, in definition order."""
    return module_markers(module, OnStart)


@dataclass
class Registry(Generic[T]):
    """Holds registered items in registration order."""

    _items: list[T] = field(default_factory=list)

    def register(self, item: T) -> None:
        self._items.append(item)

    def all(self) -> list[T]:
        """All registered items, in registration order."""
        return self._items


class SinkRegistry(Registry[Sink]):
    """Holds sinks."""


class TaskRegistry(Registry[ScheduledTask]):
    """Holds scheduled tasks."""


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
