"""A minimal Ottawa MeshBot showing how to add your own commands.

Run it against a MeshCore companion device:

    uv run examples/example_bot.py --serial /dev/ttyUSB0
    uv run examples/example_bot.py --ble AA:BB:CC:DD:EE:FF
    uv run examples/example_bot.py --tcp 192.168.1.50:5000

Then message the node "!help" (in a DM or on channel 0) to see commands.
"""

import argparse
import asyncio
import logging
import random

from ottawa_meshbot import Context, MeshBot
from ottawa_meshbot.runner import MeshCoreRunner, connect

bot = MeshBot(prefix="!")


@bot.command("ping", help="Check that the bot is alive")
async def ping(ctx: Context) -> str:
    pong = f"pong ({ctx.path_description})"
    # ctx.raw is the full meshcore payload, for fields the framework
    # doesn't model — e.g. SNR, reported by firmware protocol v3+.
    snr = (ctx.raw or {}).get("SNR")
    if snr is not None:
        pong += f" SNR {snr}dB"
    return pong


@bot.command("echo", help="Repeat back whatever you send")
async def echo(ctx: Context) -> str:
    return ctx.args or "(nothing to echo)"


@bot.command("roll", help="Roll a die, e.g. !roll 20", aliases=("dice",))
async def roll(ctx: Context) -> str:
    try:
        sides = int(ctx.args) if ctx.args else 6
    except ValueError:
        return "Usage: !roll [sides]"
    if sides < 2:
        return "A die needs at least 2 sides."
    who = ctx.sender_name or "Someone"
    return f"{who} rolled a {random.randint(1, sides)} (d{sides})"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--serial", metavar="PORT", help="serial port, e.g. /dev/ttyUSB0")
    group.add_argument("--ble", metavar="ADDRESS", help="BLE address of the device")
    group.add_argument("--tcp", metavar="HOST:PORT", help="TCP host:port")
    parser.add_argument("--baudrate", type=int, default=115200)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    mc = await connect(serial=args.serial, baudrate=args.baudrate, ble=args.ble, tcp=args.tcp)
    runner = MeshCoreRunner(bot, mc)
    try:
        await runner.run_forever()
    finally:
        await mc.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
