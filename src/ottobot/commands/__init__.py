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

import importlib
import logging
import pkgutil
from types import ModuleType

from ..bot import MeshBot
from ..registry import Command, module_commands

logger = logging.getLogger(__name__)


def iter_command_module_names() -> list[str]:
    """Names of all command modules in this package, sorted."""
    return sorted(
        info.name
        for info in pkgutil.iter_modules(__path__)
        if not info.name.startswith("_")
    )


def register_module(bot: MeshBot, module: ModuleType) -> list[Command]:
    """Register every @command-marked handler in *module* on *bot*.

    Returns the commands that were registered. Tests use this to load a
    single command module against a fresh bot.
    """
    commands = module_commands(module)
    for command in commands:
        bot.add_command(command)
    return commands


def load_commands(bot: MeshBot) -> list[str]:
    """Import every command module and register its @command handlers.

    Returns the module names that were loaded. Fails fast: a module with
    no @command-marked handlers raises TypeError, import errors propagate,
    and duplicate command names raise ValueError (from CommandRegistry).
    A broken command file should stop the bot from starting, not be
    skipped silently.
    """
    loaded: list[str] = []
    for name in iter_command_module_names():
        module = importlib.import_module(f"{__name__}.{name}")
        if not register_module(bot, module):
            raise TypeError(
                f"command module {module.__name__!r} must define at least one "
                "@command handler"
            )
        loaded.append(name)
        logger.debug("loaded command module %s", name)
    return loaded
