"""The bot's scheduled tasks — one module per task.

Each module in this package defines its handler at the top level with
the @task marker::

    @task("weather_alerts", interval=timedelta(minutes=10), channel=OTT_ALERTS)
    async def weather_alerts(ctx: TaskContext) -> str | None: ...

and is discovered and loaded automatically by load_tasks(). To add a
task, drop a new file in this directory; there is no central list to
edit. Modules whose names start with an underscore are skipped, so shared
helpers can live in e.g. _util.py.

Unlike commands and sinks, a task's handler isn't triggered by an
incoming message — the runner calls it on its own timer (see
ottobot.runner.MeshCoreRunner) and broadcasts anything it returns or
replies with.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType

from ..bot import Ottobot
from ..registry import ScheduledTask, module_tasks

logger = logging.getLogger(__name__)


def iter_module_names() -> list[str]:
    """Names of all task modules in this package, sorted."""
    return sorted(
        info.name
        for info in pkgutil.iter_modules(__path__)
        if not info.name.startswith("_")
    )


def register_module(bot: Ottobot, module: ModuleType) -> list[ScheduledTask]:
    """Register every @task-marked handler in *module* on *bot*.

    Returns the tasks that were registered. Tests use this to load a
    single task module against a fresh bot.
    """
    tasks = module_tasks(module)
    for task in tasks:
        bot.add_task(task)
    return tasks


def load_tasks(bot: Ottobot) -> list[str]:
    """Import every task module and register its @task handlers.

    Returns the module names that were loaded. Fails fast: a module with
    no @task-marked handlers raises TypeError, import errors propagate.
    A broken task file should stop the bot from starting, not be
    skipped silently.
    """
    loaded: list[str] = []
    for name in iter_module_names():
        module = importlib.import_module(f"{__name__}.{name}")
        if not register_module(bot, module):
            raise TypeError(
                f"task module {module.__name__!r} must define at least one "
                "@task handler"
            )
        loaded.append(name)
        logger.debug("loaded task module %s", name)
    return loaded
