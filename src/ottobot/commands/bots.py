"""!bots — find the bots on the mesh; answers without being addressed.

Type ``!bots`` on the #bots channel and every bot introduces itself, so you
can discover who's out there without already knowing their names (issue #43).
Unlike other commands it doesn't need to be addressed by name — that's the
whole point, one shout enumerates everyone — and it only speaks up on #bots,
so the roll call stays where the bots live.
"""

from __future__ import annotations

from ottobot import Context, command
from ottobot.channels import BOTS

# Ottobot's self-introduction. Kept to one mesh packet (~140 chars).
GREETING = (
    "hello! say @[ottobot] !help for my commands I also share alerts in "
    "#ott-alerts and mirror the public channel to discord"
)


@command(
    "bots",
    help="Find the bots on the mesh (each one introduces itself)",
    requires_address=False,
)
async def bots(ctx: Context) -> str | None:
    # Only introduce ourselves on #bots — the roll-call channel. On the
    # other command channels (#testing, #ottobot-testing) stay quiet.
    if ctx.message.channel_idx != BOTS.index:
        return None
    return GREETING
