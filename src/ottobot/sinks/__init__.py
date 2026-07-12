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

from types import ModuleType

from ..bot import Ottobot
from ..loader import (
    iter_package_module_names,
    load_marked_modules,
    register_module_markers,
)
from ..registry import Sink


def iter_module_names() -> list[str]:
    """Names of all sinks modules in this package, sorted."""
    return iter_package_module_names(__path__)


def register_module(bot: Ottobot, module: ModuleType) -> list[Sink]:
    """Register every marked handler in *module* on *bot*.

    Returns the sinks that were registered (its @on_start hooks, if any,
    are registered too). Tests use this to load a single sink module
    against a fresh bot.
    """
    return register_module_markers(bot, module, Sink)


def load_sinks(bot: Ottobot) -> list[str]:
    """Import every sink module and register its @sink handlers.

    Returns the module names that were loaded. Fails fast — a module with
    no @sink handlers raises TypeError; see
    ottobot.loader.load_marked_modules.
    """
    return load_marked_modules(
        bot, __name__, iter_module_names(), register_module, "sink"
    )
