"""!advert — broadcast an advert from this node so nearby radios learn it.

Usage: !advert [flood]

With no argument, sends a zero-hop advert: only direct neighbours hear it.
Pass "flood" to send a flood advert that propagates across the whole mesh —
use it sparingly, it costs everyone airtime.
"""

from ottobot import Context, DeviceError, command


@command("advert", help="Send an advert from the radio (!advert [flood])")
async def advert(ctx: Context) -> str:
    arg = ctx.args.strip().lower()
    if arg and arg != "flood":
        return "Usage: !advert [flood]"
    flood = arg == "flood"
    if ctx.device is None:
        return "No radio connected, can't send an advert."
    try:
        await ctx.device.send_advert(flood=flood)
    except DeviceError as exc:
        return f"Couldn't send advert: {exc}"
    return f"Sent {'flood' if flood else 'zero-hop'} advert."
