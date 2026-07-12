"""The Ottobot core: command registration and message dispatch.

This module is transport-agnostic. It knows nothing about radios or the
meshcore library — it just maps incoming messages to command handlers.
The meshcore wiring lives in ottobot.runner.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from .channels import ChannelConfig
from .config import BotConfig
from .registry import (
    Command,
    CommandHandler,
    OnStartHandler,
    ScheduledTask,
    TaskHandler,
)
from .context import Context, IncomingMessage, ReplyFunc

logger = logging.getLogger(__name__)


class Ottobot:
    """A chatbot that responds to prefixed commands, e.g. "!ping".

    Register commands with the decorator::

        bot = Ottobot(name="ottobot")

        @bot.command("ping", help="Check that the bot is alive")
        async def ping(ctx):
            return "pong"

    Handlers receive a Context and may either return a string (sent as the
    reply) or call ``await ctx.reply(...)`` themselves (e.g. for multiple
    replies). Returning None sends nothing.
    """

    def __init__(
        self,
        name: str,
        prefix: str = "!",
        config: BotConfig | None = None,
    ) -> None:
        self.prefix = prefix
        # The bot's own name on the mesh. Commands that require addressing
        # only run when the message addresses this name (e.g. "@[ottobot] !ping").
        self.name = name
        # The loaded TOML config, surfaced to handlers via Context.config.
        self.config = config or BotConfig()
        # Every command, keyed by each of its names/aliases (lowercased).
        self._commands: dict[str, Command] = {}
        # Sinks run on every message; tasks run on the runner's timer;
        # on_start hooks run once in setup().
        self.sinks: list[CommandHandler] = []
        self.tasks: list[ScheduledTask] = []
        self._on_start: list[OnStartHandler] = []
        self.add_command(
            Command(name="help", handler=self._help, help="List available commands")
        )

    def command(
        self, name: str, **kwargs
    ) -> Callable[[CommandHandler], CommandHandler]:
        """Decorator that registers a command handler.

        Keyword arguments are the optional Command fields: help, aliases,
        requires_address.
        """

        def decorator(handler: CommandHandler) -> CommandHandler:
            self.add_command(Command(name, handler, **kwargs))
            return handler

        return decorator

    def task(
        self, name: str, *, interval: timedelta, channel: ChannelConfig, help: str = ""
    ) -> Callable[[TaskHandler], TaskHandler]:
        """Decorator that registers a scheduled task handler."""

        def decorator(handler: TaskHandler) -> TaskHandler:
            self.add_task(ScheduledTask(name, handler, interval, channel, help))
            return handler

        return decorator

    def add_command(self, command: Command) -> None:
        keys = [n.lower() for n in (command.name, *command.aliases)]
        for key in keys:
            if key in self._commands:
                raise ValueError(f"command name {key!r} is already registered")
        for key in keys:
            self._commands[key] = command

    def get_command(self, name: str) -> Command | None:
        return self._commands.get(name.lower())

    def commands(self) -> list[Command]:
        """All registered commands, sorted by name (aliases deduplicated)."""
        unique = {command.name: command for command in self._commands.values()}
        return sorted(unique.values(), key=lambda c: c.name)

    def add_sink(self, sink: CommandHandler) -> None:
        self.sinks.append(sink)

    def add_task(self, task: ScheduledTask) -> None:
        self.tasks.append(task)

    def add_on_start(self, hook: OnStartHandler) -> None:
        self._on_start.append(hook)

    async def setup(self) -> None:
        """Run every registered @on_start hook once, before handling messages."""
        for hook in self._on_start:
            await hook(self)

    def parse(self, text: str) -> tuple[str, str] | None:
        """Split message text into (command name, argument string).

        Returns None if the text is not a command (wrong prefix or empty).
        """
        text = text.strip()
        if not text.startswith(self.prefix):
            return None
        body = text[len(self.prefix) :].strip()
        if not body:
            return None
        name, _, args = body.partition(" ")
        return name, args.strip()

    def strip_address(self, text: str) -> tuple[str, bool]:
        """Remove a leading address to the bot by name, e.g. "@[ottobot] !ping".

        Returns (remaining text, whether the bot was addressed by name).
        The MeshCore app inserts mentions as "@[Name]"; we also accept a
        plain or "@"-prefixed name typed by hand. A hand-typed name must
        stand alone — followed by whitespace, ":"/"," or the end — so
        "ottobotanist" isn't read as "ottobot".
        """
        text = text.strip()
        name = self.name.lower()
        # The app's mention form "@[Name]" is self-delimiting.
        mention = f"@[{name}]"
        if text.lower().startswith(mention):
            return text[len(mention) :].lstrip(" :,"), True
        # A hand-typed "@name" or bare "name", which must stand alone.
        body = text[1:] if text.startswith("@") else text
        if body.lower().startswith(name):
            rest = body[len(self.name) :]
            if not rest or rest[0] in " :,":
                return rest.lstrip(" :,"), True
        return text, False

    async def dispatch(self, message: IncomingMessage, reply: ReplyFunc) -> None:
        """Handle one incoming message."""

        sink_ctx = Context(
            message=message,
            command_name=None,
            args=message.text,
            _reply=reply,
            config=self.config,
        )
        for sink in self.sinks:
            try:
                result = await sink(sink_ctx)
            except Exception:
                logger.exception("sink %r raised", getattr(sink, "__name__", sink))
                continue
            if result is not None:
                await reply(result)

        text, addressed = self.strip_address(message.text)
        parsed = self.parse(text)
        if parsed is None:
            return
        name, args = parsed
        command = self.get_command(name)
        if command is None:
            logger.debug("ignoring unknown command %r", name)
            return
        # Only answer when addressed by name, unless the command opts out.
        if command.requires_address and not addressed:
            logger.debug(
                "ignoring channel %r: bot %r not addressed by name",
                command.name,
                self.name,
            )
            return
        ctx = Context(
            message=message,
            command_name=command.name,
            args=args,
            _reply=reply,
            config=self.config,
        )
        try:
            result = await command.handler(ctx)
        except Exception:
            logger.exception("command %r raised", command.name)
            await reply(f"Sorry, {self.prefix}{command.name} hit an error.")
            return
        if result is not None:
            await reply(result)

    async def _help(self, ctx: Context) -> str:
        lines = []
        for command in self.commands():
            entry = f"{self.prefix}{command.name}"
            if command.help:
                entry += f" - {command.help}"
            lines.append(entry)
        return "\n".join(lines)
