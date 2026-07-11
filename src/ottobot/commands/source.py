"""!source — get a link to the bot's source code."""

from ottobot import Context, command

SOURCE_URL = "https://github.com/tahnok/ottobot"


@command("source", help="Link to the bot's source code")
async def source(ctx: Context) -> str:
    return f"my source code is available at {SOURCE_URL} and contributions are welcome"
