"""!ping — check that the bot is alive and see how your message got there."""

from ottawa_meshbot import Context, command


@command("ping", help="Check that the bot is alive")
async def ping(ctx: Context) -> str:
    pong = f"pong ({ctx.path_description})"
    # ctx.raw is the full meshcore payload, for fields the framework
    # doesn't model — e.g. SNR, reported by firmware protocol v3+.
    snr = (ctx.raw or {}).get("SNR")
    if snr is not None:
        pong += f" SNR {snr}dB"
    return pong
