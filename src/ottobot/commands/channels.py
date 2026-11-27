"""!channels — list Ottawa's public MeshCore channels."""

from ottobot import Context, command
from ottobot.bot import MAX_MESSAGE_LEN

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
    # The full list doesn't fit one mesh packet, so pack whole channel names
    # into packet-sized chunks and send each as its own reply.
    chunks: list[str] = []
    current = "Channels:"
    for name in CHANNELS:
        candidate = f"{current} {name}"
        if len(candidate) <= MAX_MESSAGE_LEN:
            current = candidate
        else:
            chunks.append(current)
            current = name
    chunks.append(current)
    await ctx.reply_many(chunks)
