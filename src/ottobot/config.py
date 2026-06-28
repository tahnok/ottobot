"""Load Ottobot's TOML config: the source of truth for device settings.

The config file pins the bot's advertised name, channels, key pair, and
(optionally) radio parameters. On startup these are pushed onto the radio so
the device always matches the file — see ``runner.apply_settings``.

This module only parses and validates; it imports nothing from meshcore so
it stays easy to unit-test. Example file:

    name = "ottobot"
    private_key = "<128 hex chars>"   # optional, 64-byte key pair

    [[channels]]
    index = 0
    name = "public"
    # secret = "<32 hex chars>"       # optional 16-byte secret

    [[channels]]
    index = 1
    name = "#ott-alerts"              # weather_alerts posts here

    [radio]
    freq = 910.525
    bw = 250.0
    sf = 11
    cr = 5

    log_level = "INFO"                  # optional; DEBUG/INFO/WARNING/...

    [discord]
    webhook_url = "https://discord.com/api/webhooks/..."  # optional sink
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

PRIVATE_KEY_LEN = 64
CHANNEL_SECRET_LEN = 16


@dataclass(frozen=True)
class ChannelConfig:
    index: int
    name: str
    # 16-byte secret; None means the device derives it from the name.
    secret: bytes | None = None


@dataclass(frozen=True)
class RadioConfig:
    freq: float
    bw: float
    sf: int
    cr: int


@dataclass(frozen=True)
class BotConfig:
    name: str | None = None
    private_key: bytes | None = None
    channels: tuple[ChannelConfig, ...] = ()
    radio: RadioConfig | None = None
    # A logging level name (e.g. "DEBUG", "INFO"); None leaves the default.
    log_level: str | None = None

    # Path to the sqlite file stateful sinks (e.g. the welcome sink) use.
    # None means cli falls back to its default.
    database: Path | None = None

    # Discord incoming-webhook URL; when set, the discord sink mirrors
    # public-channel messages to it. None disables the sink.
    discord_webhook_url: str | None = None

    def channel_idx(self, name: str) -> int | None:
        """Index of the channel with this name (case-insensitive), or None."""
        for channel in self.channels:
            if channel.name.lower() == name.lower():
                return channel.index
        return None

    def public_channel_idx(self) -> int:
        """Index of the channel named "public" in this config, or 0 by default."""
        idx = self.channel_idx("public")
        return 0 if idx is None else idx


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


def _parse_channel(raw: dict) -> ChannelConfig:
    if "index" not in raw or "name" not in raw:
        raise ValueError("each [[channels]] entry needs an index and a name")
    secret_hex = raw.get("secret")
    secret = (
        _decode_hex(secret_hex, "channel secret", CHANNEL_SECRET_LEN)
        if secret_hex is not None
        else None
    )
    return ChannelConfig(index=int(raw["index"]), name=str(raw["name"]), secret=secret)


def _parse_radio(raw: dict) -> RadioConfig:
    missing = [k for k in ("freq", "bw", "sf", "cr") if k not in raw]
    if missing:
        raise ValueError(f"[radio] is missing required keys: {', '.join(missing)}")
    return RadioConfig(
        freq=float(raw["freq"]),
        bw=float(raw["bw"]),
        sf=int(raw["sf"]),
        cr=int(raw["cr"]),
    )


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
    channels = tuple(_parse_channel(c) for c in data.get("channels", ()))
    radio = _parse_radio(data["radio"]) if "radio" in data else None
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
        channels=channels,
        radio=radio,
        log_level=log_level,
        discord_webhook_url=discord_webhook_url,
        database=Path(database) if database is not None else None,
    )


def load_config(path: str | Path) -> BotConfig:
    """Read and validate the TOML config at *path*."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return parse_config(data)
