"""Posts a welcome on the public channel at fixed times each day.

Four times a day (``WELCOME_TIMES``, Ottawa time) the bot posts a short
welcome pointing at the #bots channel, where it answers commands (it stays
quiet on public — see ``channels.COMMAND_CHANNELS``). Being scheduled by
clock time rather than interval, it doesn't post when the bot starts up.
"""

from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

from ottobot import TaskContext, task
from ottobot.channels import PUBLIC

OTTAWA = ZoneInfo("America/Toronto")

# under 140 chars plz — and mention only #bots, since that's the one
# channel newcomers need to reach the bot (commands aren't answered here
# on public).
WELCOME = "Welcome to the mesh! To chat with me join the #bots channel and say '@ottobot !help'. More (incl. Discord/Matrix) at https://ottawamesh.ca"

# Midnight, 9am, 1pm and 9pm local: the midnight one is for the many
# late-night folks on the mesh.
WELCOME_TIMES = tuple(time(hour, tzinfo=OTTAWA) for hour in (0, 9, 13, 21))


@task(
    "welcome",
    at=WELCOME_TIMES,
    channel=PUBLIC,
    help="Post a welcome pointing newcomers at #bots",
)
async def welcome(ctx: TaskContext) -> str:
    return WELCOME
