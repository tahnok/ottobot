"""Greet people who say hello — an example @listener.

Unlike a command, a listener runs on *every* message regardless of prefix,
and needs no bot instance: the module-level @listener marker attaches
metadata at import time and load_commands() registers it later. Reply by
returning a str (or None to stay silent), exactly like a command.

This one only answers a bare greeting, so it stays quiet on ordinary
chatter and commands.
"""

from ottobot import Context, listener

GREETINGS = {"hi", "hello", "hey", "yo", "howdy"}


@listener
async def greet(ctx: Context) -> str | None:
    word = ctx.message.text.strip().rstrip("!.").lower()
    if word in GREETINGS:
        return "hi there! send !help to see what I can do."
    return None
