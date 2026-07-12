"""An Ottawa mesh bot for MeshCore networks, with pluggable commands."""

from .bot import Ottobot
from .registry import (
    Command,
    CommandRegistry,
    command,
    OnStart,
    on_start,
    ScheduledTask,
    Sink,
    sink,
    task,
    TaskRegistry,
)
from .context import Context, IncomingMessage, TaskContext

__all__ = [
    "Ottobot",
    "Command",
    "CommandRegistry",
    "Context",
    "IncomingMessage",
    "command",
    "ScheduledTask",
    "OnStart",
    "on_start",
    "Sink",
    "sink",
    "task",
    "TaskContext",
    "TaskRegistry",
]
