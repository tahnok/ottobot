"""!channels — list Ottawa's public MeshCore channels."""

from ottobot import Context, command

# Public channels from https://ottawamesh.ca/meshcore/general-public-channels/
# plus the #ott-alerts channel. This is a user-facing directory of channels to
# join, not the set the bot itself tunes to (see ottobot.channels.CHANNELS).
CHANNELS = (
    "#aircraft",
    "#altavista",
    "#aylmer",
    "#bike",
    "#bots",
    "#caf",
    "#cars",
    "#constancebay",
    "#demoscene",
    "#games",
    "#hamradio",
    "#hike",
    "#music",
    "#onqc",
    "#operators",
    "#ottawa",
    "#ott-alerts",
    "#radio",
    "#testing",
    "#wardriving",
    "#watersports",
    "#queer",
)


@command("channels", help="List Ottawa's public MeshCore channels")
async def channels(ctx: Context) -> str:
    return "Channels: " + " ".join(CHANNELS)
