"""Load Ottobot's TOML config: the source of truth for per-bot settings.

The config file pins the bot's advertised name and key pair. On startup these
are pushed onto the device so it always matches the file — see
``runner.apply_settings``. The channels and radio preset are not per-bot config;
they live in ``ottobot.channels`` (see ``CHANNELS``) and ``ottobot.radio`` (see
``RADIO``) so every Ottawa bot shares them.

This module only parses and validates; it imports nothing from meshcore so
it stays easy to unit-test. Example file:

    name = "ottobot"
    private_key = "<128 hex chars>"   # optional, 64-byte key pair

    log_level = "INFO"                  # optional; DEBUG/INFO/WARNING/...

    [discord]
    webhook_url = "https://discord.com/api/webhooks/..."  # optional sink
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ottobot.channels import CHANNELS, ChannelConfig

PRIVATE_KEY_LEN = 64


@dataclass(frozen=True)
class BotConfig:
    name: str | None = None
    private_key: bytes | None = None
    channels: tuple[ChannelConfig, ...] = CHANNELS
    # A logging level name (e.g. "DEBUG", "INFO"); None leaves the default.
    log_level: str | None = None

    # Path to the sqlite file stateful sinks (e.g. the welcome sink) use.
    # None means cli falls back to its default.
    database: Path | None = None

    # Discord incoming-webhook URL; when set, the discord sink mirrors
    # public-channel messages to it. None disables the sink.
    discord_webhook_url: str | None = None


def _decode_hex(value: str, field_name: str, expected_len: int) -> bytes:
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not valid hex: {exc}") from exc
    if len(raw) != expected_len:
        raise ValueError(
            f"{field_name} must be {expected_len} bytes "
            f"({expected_len * 2} hex chars), got {len(raw)}"
        )
    return raw


def _parse_log_level(value: object) -> str:
    name = str(value).upper()
    if name not in logging.getLevelNamesMapping():
        valid = ", ".join(sorted(logging.getLevelNamesMapping()))
        raise ValueError(f"log_level {value!r} is not a known level (one of: {valid})")
    return name


def parse_config(data: dict) -> BotConfig:
    """Build a BotConfig from already-parsed TOML data."""
    private_key_hex = data.get("private_key")
    private_key = (
        _decode_hex(private_key_hex, "private_key", PRIVATE_KEY_LEN)
        if private_key_hex is not None
        else None
    )
    name = data.get("name")
    log_level_raw = data.get("log_level")
    log_level = _parse_log_level(log_level_raw) if log_level_raw is not None else None
    discord = data.get("discord", {})
    webhook_url = discord.get("webhook_url")
    discord_webhook_url = str(webhook_url) if webhook_url is not None else None
    database = data.get("database")

    return BotConfig(
        name=str(name) if name is not None else None,
        private_key=private_key,
        log_level=log_level,
        discord_webhook_url=discord_webhook_url,
        database=Path(database) if database is not None else None,
    )


def load_config(path: str | Path) -> BotConfig:
    """Read and validate the TOML config at *path*."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return parse_config(data)
