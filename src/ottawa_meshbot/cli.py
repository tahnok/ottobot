"""Run the Ottawa MeshBot against a MeshCore companion device.

    ottawa-meshbot --serial /dev/ttyUSB0
    ottawa-meshbot --ble AA:BB:CC:DD:EE:FF
    ottawa-meshbot --tcp 192.168.1.50:5000

Then message the node "!help" (in a DM or on channel 0) to see commands.

To try commands locally without a device or touching the mesh:

    ottawa-meshbot --simulate
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from .bot import MeshBot
from .commands import load_commands
from .runner import MeshCoreRunner, connect
from .simulator import Simulator


def build_bot(prefix: str = "!") -> MeshBot:
    """A MeshBot with every command in ottawa_meshbot.commands loaded."""
    bot = MeshBot(prefix=prefix)
    load_commands(bot)
    return bot


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ottawa-meshbot",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--serial", metavar="PORT", help="serial port, e.g. /dev/ttyUSB0")
    group.add_argument("--ble", metavar="ADDRESS", help="BLE address of the device")
    group.add_argument("--tcp", metavar="HOST:PORT", help="TCP host:port")
    group.add_argument(
        "--simulate",
        action="store_true",
        help="chat with the bot in an in-memory REPL instead of a device",
    )
    parser.add_argument("--baudrate", type=int, default=115200)
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    bot = build_bot()
    if args.simulate:
        await Simulator(bot).repl()
        return
    mc = await connect(serial=args.serial, baudrate=args.baudrate, ble=args.ble, tcp=args.tcp)
    runner = MeshCoreRunner(bot, mc)
    try:
        await runner.run_forever()
    finally:
        await mc.disconnect()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
