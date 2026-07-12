"""The bot's commands — one module per command.

Each module in this package defines its handlers at the top level with
the @command marker::

    @command("ping", help="Check that the bot is alive")
    async def ping(ctx: Context) -> str: ...

and is discovered and loaded automatically by load_commands(). To add a
command, drop a new file in this directory; there is no central list to
edit. Modules whose names start with an underscore are skipped, so shared
helpers can live in e.g. _util.py.
"""

from __future__ import annotations

from types import ModuleType

from ..bot import Ottobot
from ..loader import (
    iter_package_module_names,
    load_marked_modules,
    register_module_markers,
)
from ..registry import Command


def iter_command_module_names() -> list[str]:
    """Names of all command modules in this package, sorted."""
    return iter_package_module_names(__path__)


def register_module(bot: Ottobot, module: ModuleType) -> list[Command]:
    """Register every marked handler in *module* on *bot*.

    Returns the commands that were registered. Tests use this to load a
    single command module against a fresh bot.
    """
    return register_module_markers(bot, module, Command)


def load_commands(bot: Ottobot) -> list[str]:
    """Import every command module and register its @command handlers.

    Returns the module names that were loaded. Fails fast — a module with
    no @command handlers raises TypeError; see
    ottobot.loader.load_marked_modules.
    """
    return load_marked_modules(
        bot, __name__, iter_command_module_names(), register_module, "command"
    )
