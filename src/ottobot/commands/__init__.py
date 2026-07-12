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

import sys
from types import ModuleType

from ..bot import Ottobot
from ..discovery import load_package
from ..registry import Command, module_commands


def register_module(bot: Ottobot, module: ModuleType) -> list[Command]:
    """Register every @command-marked handler in *module* on *bot*.

    Returns the commands that were registered. Tests use this to load a
    single command module against a fresh bot.
    """
    commands = module_commands(module)
    for command in commands:
        bot.add_command(command)
    return commands


def load_commands(bot: Ottobot) -> list[str]:
    """Import every command module and register its @command handlers.

    Returns the module names that were loaded. Duplicate command names
    raise ValueError (from Ottobot.add_command); see
    discovery.load_package for the other failure modes.
    """
    return load_package(bot, sys.modules[__name__], register_module, "@command")
