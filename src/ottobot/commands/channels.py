"""!channels — list Ottawa's public MeshCore channels."""

from ottobot import Context, command
from ottobot.messages import pack_messages

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
    "#queer",
    "#radio",
    "#testing",
    "#wardriving",
    "#watersports",
)


@command("channels", help="List Ottawa's public MeshCore channels")
async def channels(ctx: Context) -> None:
    await ctx.reply_many(pack_messages(["Channels:", *CHANNELS]))
