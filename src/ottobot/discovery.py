"""Auto-discovery of the modules in a handler package.

Commands, sinks, and scheduled tasks all follow the same convention: one
module per handler, dropped into the package directory with no central
list to edit. This module implements that convention once; the
ottobot.commands / ottobot.sinks / ottobot.tasks packages each wrap it
with their kind-specific registration.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable, Iterable, Sized
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bot import Ottobot

logger = logging.getLogger(__name__)


def iter_module_names(path: Iterable[str]) -> list[str]:
    """Names of the modules on a package's ``__path__``, sorted.

    Modules whose names start with an underscore are skipped, so shared
    helpers can live in e.g. _util.py.
    """
    return sorted(
        info.name
        for info in pkgutil.iter_modules(path)
        if not info.name.startswith("_")
    )


def load_package(
    bot: "Ottobot",
    package: ModuleType,
    register: Callable[["Ottobot", ModuleType], Sized],
    kind: str,
) -> list[str]:
    """Import every module in *package* and register its handlers on *bot*.

    *register* does the kind-specific registration and returns what it
    registered. Returns the module names that were loaded. Fails fast: a
    module registering nothing raises TypeError and import errors
    propagate — a broken handler file should stop the bot from starting,
    not be skipped silently.
    """
    loaded: list[str] = []
    for name in iter_module_names(package.__path__):
        module = importlib.import_module(f"{package.__name__}.{name}")
        if not register(bot, module):
            raise TypeError(
                f"module {module.__name__!r} must define at least one "
                f"{kind} handler"
            )
        loaded.append(name)
        logger.debug("loaded %s module %s", kind, name)
    return loaded
