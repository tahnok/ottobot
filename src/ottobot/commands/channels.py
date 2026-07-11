"""!channels — list Ottawa's public MeshCore channels."""

from ottobot import Context, command
from ottobot.channels import CHANNELS

# The public, "#"-prefixed channels from the shared channel list (skips the
# MeshCore default "public" channel, which isn't one users tune to by name).
PUBLIC_CHANNELS = tuple(c.name for c in CHANNELS if c.name.startswith("#"))


@command("channels", help="List Ottawa's public MeshCore channels")
async def channels(ctx: Context) -> str:
    return "Channels: " + " ".join(PUBLIC_CHANNELS)
