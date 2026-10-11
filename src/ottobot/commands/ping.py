"""!ping — check that the bot is alive and see how your message got there.

When the bot can tell which packet the ping arrived in, the reply links to
it on Beacon, which shows every observer that heard it and the routes it took.
"""

from ottobot import Context, command
from ottobot.beacon import packet_url
from ottobot.bot import MAX_MESSAGE_LEN


@command("ping", help="Check that the bot is alive")
async def ping(ctx: Context) -> str:
    who = ctx.sender_name or "you"
    reply = f"@[{who}] pong ({ctx.path_description})"
    packet_hash = ctx.message.packet_hash
    if packet_hash is None:
        return reply
    link = packet_url(packet_hash)
    if len(f"{reply} {link}") > MAX_MESSAGE_LEN:
        # A long route doesn't fit beside the link; Beacon shows it anyway.
        reply = f"@[{who}] pong ({ctx.message.hop_count} hops)"
    return f"{reply} {link}"
