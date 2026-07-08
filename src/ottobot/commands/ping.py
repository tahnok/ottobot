"""!ping — check that the bot is alive and see how your message got there."""

from ottobot import Context, command


@command("ping", help="Check that the bot is alive")
async def ping(ctx: Context) -> str:
    who = ctx.sender_name or "you"
    return f"@[{who}] pong ({ctx.path_description})"
