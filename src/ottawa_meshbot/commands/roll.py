"""!roll — roll a die, e.g. !roll 20."""

import random

from ottawa_meshbot import Context, command


@command("roll", help="Roll a die, e.g. !roll 20", aliases=("dice",))
async def roll(ctx: Context) -> str:
    try:
        sides = int(ctx.args) if ctx.args else 6
    except ValueError:
        return "Usage: !roll [sides]"
    if sides < 2:
        return "A die needs at least 2 sides."
    who = ctx.sender_name or "Someone"
    return f"{who} rolled a {random.randint(1, sides)} (d{sides})"
