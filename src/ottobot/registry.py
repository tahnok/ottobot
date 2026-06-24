"""Command registration and lookup."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Context

CommandHandler = Callable[["Context"], Awaitable[str | None]]

# A listener has the same shape as a command handler: it takes a Context and
# may return a string to send as a reply (or None to stay silent). Unlike a
# command it has no name and runs on *every* message, not just prefixed ones.
MessageListener = Callable[["Context"], Awaitable[str | None]]

_COMMAND_ATTR = "_meshbot_command"
_LISTENER_ATTR = "_meshbot_listener"


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


def listener(handler: MessageListener) -> MessageListener:
    """Mark a module-level coroutine to run on *every* incoming message.

    A listener runs for each message the bot handles, regardless of prefix
    or command name, and may return a string to reply (or call
    ``ctx.reply(...)``). It has no name and never appears in help. Used as a
    bare decorator::

        @listener
        async def greet(ctx: Context) -> str | None:
            if "hello" in ctx.message.text.lower():
                return "hi there!"

    Like @command, this only attaches metadata at import time; load_commands()
    later collects marked listeners via module_listeners() and registers them.
    """

    setattr(handler, _LISTENER_ATTR, True)
    return handler


def module_listeners(module: ModuleType) -> list[MessageListener]:
    """The @listener-marked coroutines defined in *module*, in definition order.

    Mirrors module_commands(): handlers merely imported into the module are
    excluded so importing another module's listener can't register it twice.
    """
    return [
        obj
        for obj in vars(module).values()
        if getattr(obj, _LISTENER_ATTR, False)
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
