"""Periodically posts a welcome on the public channel.

Every ``WELCOME_INTERVAL`` (four times a day) the bot posts a short
welcome pointing at the #bots channel, where it answers commands (it stays
quiet on public — see ``channels.COMMAND_CHANNELS``). The runner calls
every task once right at startup; this one stays quiet on that first call,
so restarting the bot doesn't add an extra welcome to the channel.
"""

from __future__ import annotations

from datetime import timedelta

from ottobot import TaskContext, task
from ottobot.channels import PUBLIC

# under 140 chars plz — and mention only #bots, since that's the one
# channel newcomers need to reach the bot (commands aren't answered here
# on public).
WELCOME = "New to the mesh? To chat with me join the #bots channel and say '@ottobot !help'. More (incl. Discord/Matrix) at https://ottawamesh.ca"

# Four announcements a day.
WELCOME_INTERVAL = timedelta(hours=6)

# Whether the startup call (the one to skip) has happened yet.
_started = False


@task(
    "welcome",
    interval=WELCOME_INTERVAL,
    channel=PUBLIC,
    help="Post a welcome pointing newcomers at #bots",
)
async def welcome(ctx: TaskContext) -> str | None:
    global _started
    if not _started:
        _started = True
        return None
    return WELCOME
