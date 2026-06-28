"""Run Ottobot against a MeshCore companion device.

    ottobot --serial /dev/ttyUSB0
    ottobot --ble AA:BB:CC:DD:EE:FF
    ottobot --tcp 192.168.1.50:5000

Then message the node "!help" (in a DM or on channel 0) to see commands.

Pass --config ottobot.toml to make a TOML file the source of truth for the
bot's name, channels, key pair, and radio params; those settings are pushed
onto the device on startup. See ottobot.example.toml for the format.

To try commands locally without a device or touching the mesh:

    ottobot --simulate
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from .bot import MeshBot
from .commands import load_commands
from .config import BotConfig, load_config
from .runner import MeshCoreRunner, apply_settings, connect
from .simulator import Simulator

logger = logging.getLogger(__name__)


def build_bot(name: str, prefix: str = "!") -> MeshBot:
    """A MeshBot named *name* with every command in ottobot.commands loaded."""
    bot = MeshBot(name=name, prefix=prefix)
    load_commands(bot)
    return bot


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ottobot",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--serial", metavar="PORT", help="serial port, e.g. /dev/ttyUSB0"
    )
    group.add_argument("--ble", metavar="ADDRESS", help="BLE address of the device")
    group.add_argument("--tcp", metavar="HOST:PORT", help="TCP host:port")
    group.add_argument(
        "--simulate",
        action="store_true",
        help="chat with the bot in an in-memory REPL instead of a device",
    )
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument(
        "--name",
        metavar="NAME",
        help="bot name for channel addressing (default: the device's own name)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="TOML config file applied to the device on startup",
    )
    return parser.parse_args(argv)


def log_channels(name: str, config: BotConfig) -> None:
    """Log the bot's name and the channels it's configured for, on startup."""
    if config.channels:
        summary = ", ".join(
            f"{c.index}:{c.name}" + ("" if c.secret is None else " (custom secret)")
            for c in config.channels
        )
        logger.info("bot %r configured for channels %s", name, summary)
    else:
        logger.info("bot %r: no channels configured", name)


async def run(args: argparse.Namespace) -> None:
    config = load_config(args.config) if args.config else BotConfig()
    if config.log_level:
        logging.getLogger().setLevel(config.log_level)
    if args.simulate:
        # No device to ask, so fall back to the config name or a default.
        name = args.name or config.name or "ottobot"
        await Simulator(build_bot(name=name)).repl()
        return
    mc = await connect(
        serial=args.serial, baudrate=args.baudrate, ble=args.ble, tcp=args.tcp
    )
    try:
        await apply_settings(mc, config)
        # Pin the name with --name, then the config, otherwise take the
        # device's own name so addressing tracks whatever the node advertises.
        name = args.name or config.name or (mc.self_info or {}).get("name")
        if not name:
            raise SystemExit(
                "could not determine the bot's name: the device reports none. "
                "Pass --name or set name in the config to set one."
            )
        log_channels(name, config)
        runner = MeshCoreRunner(build_bot(name=name), mc)
        await runner.run_forever()
    finally:
        await mc.disconnect()


def main() -> None:
    args = parse_args()
    # Default level; the config may raise it or lower it once loaded (see run()).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
