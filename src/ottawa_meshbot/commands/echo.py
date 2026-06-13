"""!echo — repeat back whatever you send."""

from ottawa_meshbot import Context, command


@command("echo", help="Repeat back whatever you send")
async def echo(ctx: Context) -> str:
    return ctx.args or "(nothing to echo)"
