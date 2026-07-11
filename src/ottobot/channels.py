"""The MeshCore channels Ottobot knows about — the single source of truth.

The set of channels is the same for every Ottawa bot, so it lives in code
rather than per-bot TOML config: commands (e.g. ``!channels``) and scheduled
tasks (e.g. ``weather_alerts``, which posts to ``#ott-alerts``) can refer to
it directly, and ``runner.apply_settings`` pushes it onto the device on
startup so the radio is always tuned to these channels.

``index`` is the device channel slot. ``name`` is how the channel is
addressed; a ``None`` ``secret`` lets the device derive the key from the name
(the MeshCore default for ``#``-prefixed names). ``"public"`` is the MeshCore
default public channel, which ``runner`` keys with the well-known public key.

Public channels from https://ottawamesh.ca/meshcore/general-public-channels/
plus the #ott-alerts channel that weather alerts are broadcast on.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelConfig:
    index: int
    name: str
    # 16-byte secret; None means the device derives it from the name.
    secret: bytes | None = None


CHANNELS: tuple[ChannelConfig, ...] = (
    ChannelConfig(0, "public"),
    ChannelConfig(1, "#ottawa"),
    ChannelConfig(2, "#testing"),
    ChannelConfig(3, "#hike"),
    ChannelConfig(4, "#bike"),
    ChannelConfig(5, "#hamradio"),
    ChannelConfig(6, "#games"),
    ChannelConfig(7, "#aircraft"),
    ChannelConfig(8, "#watersports"),
    ChannelConfig(9, "#ott-alerts"),
)
