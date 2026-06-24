"""The MeshBot core: command registration and message dispatch.

This module is transport-agnostic. It knows nothing about radios or the
meshcore library — it just maps incoming messages to command handlers.
The meshcore wiring lives in ottobot.runner.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .registry import (
    Command,
    CommandHandler,
    CommandRegistry,
    MessageListener,
)
from .context import Context, IncomingMessage, ReplyFunc

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
        respond_in_channels: bool = True,
    ) -> None:
        self.prefix = prefix
        # The bot's own name on the mesh. In channels, commands that
        # require addressing only run when the message addresses this name
        # (e.g. "@[ottobot] !ping").
        self.name = name
        self.respond_in_channels = respond_in_channels
        self.registry = CommandRegistry()
        # Listeners run on every incoming message, before command dispatch.
        self._listeners: list[MessageListener] = []
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

    def listener(self, handler: MessageListener) -> MessageListener:
        """Decorator that registers a message listener.

        The handler runs on every message the bot handles, regardless of
        prefix or command name, and may reply by returning a string or
        calling ``ctx.reply(...)``::

            @bot.listener
            async def log_all(ctx):
                logging.info("saw: %s", ctx.message.text)
        """

        self.add_listener(handler)
        return handler

    def add_listener(self, handler: MessageListener) -> None:
        """Register a coroutine to run on every incoming message."""
        self._listeners.append(handler)

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

    async def dispatch(self, message: IncomingMessage, reply: ReplyFunc) -> bool:
        """Handle one incoming message.

        Runs every registered listener first (each sees the message
        regardless of prefix or command), then dispatches a command if the
        text is one. Returns True if the message was handled — a command ran
        or a listener replied.
        """
        if not message.is_dm and not self.respond_in_channels:
            return False
        replied = False

        async def tracking_reply(text: str) -> None:
            nonlocal replied
            replied = True
            await reply(text)

        await self._run_listeners(message, tracking_reply)
        text, addressed = self.strip_address(message.text)
        parsed = self.parse(text)
        if parsed is None:
            return replied
        name, args = parsed
        command = self.registry.get(name)
        if command is None:
            logger.debug("ignoring unknown command %r", name)
            return replied
        # On a shared channel, only answer when addressed by name (unless
        # the command opts out). DMs are always addressed to the bot.
        if not message.is_dm and command.requires_address and not addressed:
            logger.debug(
                "ignoring channel %r: bot %r not addressed by name",
                command.name,
                self.name,
            )
            return replied
        ctx = Context(
            message=message, command_name=command.name, args=args, _reply=tracking_reply
        )
        try:
            result = await command.handler(ctx)
        except Exception:
            logger.exception("command %r raised", command.name)
            await tracking_reply(f"Sorry, {self.prefix}{command.name} hit an error.")
            return True
        if result is not None:
            await tracking_reply(result)
        return True

    async def _run_listeners(self, message: IncomingMessage, reply: ReplyFunc) -> None:
        """Run every listener on *message*. One failing listener never stops
        the others or command dispatch; for listeners there is no command name
        (it is "") and args is the full message text."""
        for handler in self._listeners:
            ctx = Context(
                message=message, command_name="", args=message.text, _reply=reply
            )
            try:
                result = await handler(ctx)
            except Exception:
                logger.exception(
                    "listener %r raised", getattr(handler, "__name__", handler)
                )
                continue
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
