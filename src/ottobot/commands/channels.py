"""!channels — list Ottawa's public MeshCore channels."""

from ottobot import Context, command

# Public channels from https://ottawamesh.ca/meshcore/general-public-channels/
# plus the #ott-alerts channel.
CHANNELS = (
    "#ottawa",
    "#testing",
    "#hike",
    "#bike",
    "#hamradio",
    "#games",
    "#aircraft",
    "#watersports",
    "#ott-alerts",
)


@command("channels", help="List Ottawa's public MeshCore channels")
async def channels(ctx: Context) -> str:
    return "Channels: " + " ".join(CHANNELS)
