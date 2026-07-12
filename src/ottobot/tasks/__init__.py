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

from types import ModuleType

from ..bot import Ottobot
from ..loader import (
    iter_package_module_names,
    load_marked_modules,
    register_module_markers,
)
from ..registry import ScheduledTask


def iter_module_names() -> list[str]:
    """Names of all task modules in this package, sorted."""
    return iter_package_module_names(__path__)


def register_module(bot: Ottobot, module: ModuleType) -> list[ScheduledTask]:
    """Register every marked handler in *module* on *bot*.

    Returns the tasks that were registered. Tests use this to load a
    single task module against a fresh bot.
    """
    return register_module_markers(bot, module, ScheduledTask)


def load_tasks(bot: Ottobot) -> list[str]:
    """Import every task module and register its @task handlers.

    Returns the module names that were loaded. Fails fast — a module with
    no @task handlers raises TypeError; see
    ottobot.loader.load_marked_modules.
    """
    return load_marked_modules(
        bot, __name__, iter_module_names(), register_module, "task"
    )
