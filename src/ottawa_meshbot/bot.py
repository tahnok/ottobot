"""The MeshBot core: command registration and message dispatch.

This module is transport-agnostic. It knows nothing about radios or the
meshcore library — it just maps incoming messages to command handlers.
The meshcore wiring lives in ottawa_meshbot.runner.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .registry import Command, CommandHandler, CommandRegistry
from .context import Context, IncomingMessage, ReplyFunc

logger = logging.getLogger(__name__)


class MeshBot:
    """A chatbot that responds to prefixed commands, e.g. "!ping".

    Register commands with the decorator::

        bot = MeshBot()

        @bot.command("ping", help="Check that the bot is alive")
        async def ping(ctx):
            return "pong"

    Handlers receive a Context and may either return a string (sent as the
    reply) or call ``await ctx.reply(...)`` themselves (e.g. for multiple
    replies). Returning None sends nothing.
    """

    def __init__(self, prefix: str = "!", respond_in_channels: bool = True) -> None:
        self.prefix = prefix
        self.respond_in_channels = respond_in_channels
        self.registry = CommandRegistry()
        self.add_command(
            Command(name="help", handler=self._help, help="List available commands")
        )

    def command(
        self, name: str, *, help: str = "", aliases: tuple[str, ...] = ()
    ) -> Callable[[CommandHandler], CommandHandler]:
        """Decorator that registers a command handler."""

        def decorator(handler: CommandHandler) -> CommandHandler:
            self.add_command(
                Command(name=name, handler=handler, help=help, aliases=aliases)
            )
            return handler

        return decorator

    def add_command(self, command: Command) -> None:
        self.registry.register(command)

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

    async def dispatch(self, message: IncomingMessage, reply: ReplyFunc) -> bool:
        """Handle one incoming message. Returns True if a command ran."""
        if not message.is_dm and not self.respond_in_channels:
            return False
        parsed = self.parse(message.text)
        if parsed is None:
            return False
        name, args = parsed
        command = self.registry.get(name)
        if command is None:
            logger.debug("ignoring unknown command %r", name)
            return False
        ctx = Context(message=message, command_name=command.name, args=args, _reply=reply)
        try:
            result = await command.handler(ctx)
        except Exception:
            logger.exception("command %r raised", command.name)
            await reply(f"Sorry, {self.prefix}{command.name} hit an error.")
            return True
        if result is not None:
            await reply(result)
        return True

    async def _help(self, ctx: Context) -> str:
        lines = []
        for command in self.registry.all():
            entry = f"{self.prefix}{command.name}"
            if command.help:
                entry += f" - {command.help}"
            lines.append(entry)
        return "\n".join(lines)
