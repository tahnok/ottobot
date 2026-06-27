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

import importlib
import logging
import pkgutil
from types import ModuleType

from ..bot import MeshBot
from ..registry import Sink, module_sinks

logger = logging.getLogger(__name__)


def iter_module_names() -> list[str]:
    """Names of all sinks modules in this package, sorted."""
    return sorted(
        info.name
        for info in pkgutil.iter_modules(__path__)
        if not info.name.startswith("_")
    )


def register_module(bot: MeshBot, module: ModuleType) -> list[Sink]:
    """Register every @sink-marked handler in *module* on *bot*.

    Returns the sinks that were registered. Tests use this to load a
    single sink module against a fresh bot.
    """
    sinks = module_sinks(module)
    for sink in sinks:
        bot.add_sink(sink)
    return sinks


def load_sinks(bot: MeshBot) -> list[str]:
    """Import every sink module and register its @sink handlers.

    Returns the module names that were loaded. Fails fast: a module with
    no @sink-marked handlers raises TypeError, import errors propagate,
    A broken sink file should stop the bot from starting, not be
    skipped silently.
    """
    loaded: list[str] = []
    for name in iter_module_names():
        module = importlib.import_module(f"{__name__}.{name}")
        if not register_module(bot, module):
            raise TypeError(
                f"sink module {module.__name__!r} must define at least one "
                "@sink handler"
            )
        loaded.append(name)
        logger.debug("loaded sink module %s", name)
    return loaded
