"""The bot's sinks — one module per sink.

Each module in this package defines its handlers at the top level with
the @sink marker::

    @sink()
    async def logger(ctx: Context) -> str: ...

and is discovered and loaded automatically by load_sinks(). To add a
sink, drop a new file in this directory; there is no central list to
edit. Modules whose names start with an underscore are skipped, so shared
helpers can live in e.g. _util.py.
"""

from __future__ import annotations

import sys
from types import ModuleType

from ..bot import Ottobot
from ..discovery import load_package
from ..registry import CommandHandler, module_on_start, module_sinks


def register_module(bot: Ottobot, module: ModuleType) -> list[CommandHandler]:
    """Register a sink module's @sink and @on_start handlers on *bot*.

    Returns the sinks that were registered (its @on_start hooks, if any, are
    registered too). Tests use this to load a single sink module against a
    fresh bot.
    """
    sinks = module_sinks(module)
    for sink in sinks:
        bot.add_sink(sink)
    for hook in module_on_start(module):
        bot.add_on_start(hook)
    return sinks


def load_sinks(bot: Ottobot) -> list[str]:
    """Import every sink module and register its @sink handlers.

    Returns the module names that were loaded; see discovery.load_package
    for the failure modes.
    """
    return load_package(bot, sys.modules[__name__], register_module, "@sink")
