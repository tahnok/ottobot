"""Command registration and lookup."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Context

CommandHandler = Callable[["Context"], Awaitable[str | None]]

_COMMAND_ATTR = "_meshbot_command"


@dataclass
class Command:
    """A named command and the coroutine that handles it."""

    name: str
    handler: CommandHandler
    help: str = ""
    aliases: tuple[str, ...] = ()


def command(
    name: str, *, help: str = "", aliases: tuple[str, ...] = ()
) -> Callable[[CommandHandler], CommandHandler]:
    """Mark a module-level coroutine as a command handler.

    This only attaches metadata to the function — no bot instance is
    needed at import time. The command modules under
    ottawa_meshbot.commands use this; load_commands() later collects the
    marked handlers via module_commands() and registers them on the bot.
    """

    def decorator(handler: CommandHandler) -> CommandHandler:
        setattr(
            handler,
            _COMMAND_ATTR,
            Command(name=name, handler=handler, help=help, aliases=aliases),
        )
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
