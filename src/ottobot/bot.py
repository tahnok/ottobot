"""The MeshBot core: command registration and message dispatch.

This module is transport-agnostic. It knows nothing about radios or the
meshcore library — it just maps incoming messages to command handlers.
The meshcore wiring lives in ottobot.runner.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .registry import Command, CommandHandler, CommandRegistry, Sink, SinkRegistry
from .context import Context, Device, IncomingMessage, ReplyFunc

logger = logging.getLogger(__name__)


class MeshBot:
    """A chatbot that responds to prefixed commands, e.g. "!ping".

    Register commands with the decorator::

        bot = MeshBot(name="ottobot")

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
        respond_in_channels: bool = True,  # TODO: remove this
    ) -> None:
        self.prefix = prefix
        # The bot's own name on the mesh. In channels, commands that
        # require addressing only run when the message addresses this name
        # (e.g. "@[ottobot] !ping").
        self.name = name
        self.respond_in_channels = respond_in_channels
        # The connected radio, set by whatever drives the bot (the runner
        # for a real device). Commands reach it via ctx.device; None when
        # there is no device, as in the simulator or most tests.
        self.device: Device | None = None
        self.registry = CommandRegistry()
        self.sink_registry = SinkRegistry()
        self.add_command(
            Command(name="help", handler=self._help, help="List available commands")
        )

    def command(
        self,
        name: str,
        *,
        help: str = "",
        aliases: tuple[str, ...] = (),
        requires_address: bool = True,
    ) -> Callable[[CommandHandler], CommandHandler]:
        """Decorator that registers a command handler."""

        def decorator(handler: CommandHandler) -> CommandHandler:
            self.add_command(
                Command(
                    name=name,
                    handler=handler,
                    help=help,
                    aliases=aliases,
                    requires_address=requires_address,
                )
            )
            return handler

        return decorator

    def add_command(self, command: Command) -> None:
        self.registry.register(command)

    def add_sink(self, sink: Sink) -> None:
        self.sink_registry.register(sink)

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
            device=self.device,
        )
        for sink in self.sink_registry.all():
            try:
                result = await sink.handler(sink_ctx)
            except Exception:
                logger.exception(
                    "sink %r raised", getattr(sink.handler, "__name__", sink.handler)
                )
                continue
            if result is not None:
                await reply(result)

        if not message.is_dm and not self.respond_in_channels:
            return
        text, addressed = self.strip_address(message.text)
        parsed = self.parse(text)
        if parsed is None:
            return
        name, args = parsed
        command = self.registry.get(name)
        if command is None:
            logger.debug("ignoring unknown command %r", name)
            return
        # On a shared channel, only answer when addressed by name (unless
        # the command opts out). DMs are always addressed to the bot.
        if not message.is_dm and command.requires_address and not addressed:
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
            device=self.device,
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
        for command in self.registry.all():
            entry = f"{self.prefix}{command.name}"
            if command.help:
                entry += f" - {command.help}"
            lines.append(entry)
        return "\n".join(lines)
