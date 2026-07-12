"""Shared plugin discovery and loading for the command/sink/task packages.

ottobot.commands, ottobot.sinks, and ottobot.tasks are all packages of
one-module-per-handler plugins, discovered the same way. The discovery,
registration, and fail-fast loading logic lives here; the packages
themselves are thin wrappers that pin down their marker kind.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable, Iterable
from types import ModuleType
from typing import TYPE_CHECKING, TypeVar

from .registry import Marker, module_handlers

if TYPE_CHECKING:
    from .bot import Ottobot

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=Marker)


def iter_package_module_names(path: Iterable[str]) -> list[str]:
    """Names of a plugin package's modules (its ``__path__``), sorted.

    Modules whose names start with an underscore are skipped, so shared
    helpers can live in e.g. _util.py.
    """
    return sorted(
        info.name
        for info in pkgutil.iter_modules(path)
        if not info.name.startswith("_")
    )


def register_module_markers(
    bot: "Ottobot", module: ModuleType, kind: type[M]
) -> list[M]:
    """Register every marked handler in *module* on *bot*.

    Markers of every kind are registered (an @on_start hook alongside a
    @sink handler, say); the returned list contains just the *kind*
    markers, which load_marked_modules() uses to check that a plugin
    module defines what its package is for.
    """
    markers = module_handlers(module)
    for marker in markers:
        bot.add_marker(marker)
    return [marker for marker in markers if isinstance(marker, kind)]


def load_marked_modules(
    bot: "Ottobot",
    package_name: str,
    module_names: Iterable[str],
    register: Callable[["Ottobot", ModuleType], list[M]],
    kind_name: str,
) -> list[str]:
    """Import each named module of a plugin package and register its handlers.

    Returns the module names that were loaded. Fails fast: a module whose
    *register* call registers nothing raises TypeError, import errors
    propagate, and duplicate command names raise ValueError (from
    CommandRegistry). A broken plugin file should stop the bot from
    starting, not be skipped silently.
    """
    loaded: list[str] = []
    for name in module_names:
        module = importlib.import_module(f"{package_name}.{name}")
        if not register(bot, module):
            raise TypeError(
                f"{kind_name} module {module.__name__!r} must define at least "
                f"one @{kind_name} handler"
            )
        loaded.append(name)
        logger.debug("loaded %s module %s", kind_name, name)
    return loaded
