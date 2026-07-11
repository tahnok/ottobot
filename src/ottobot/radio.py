"""The MeshCore radio parameters Ottobot tunes its device to — the source of truth.

These pin the "region": the frequency, bandwidth, spreading factor, and coding
rate of the local mesh's preset. Like the channels in ``ottobot.channels``, they
are the same for every Ottawa bot, so they live in code rather than per-bot TOML
config — ``runner.apply_settings`` pushes them onto the device on startup so it
always matches this file. A device tuned to different radio parameters can't
hear or be heard by the rest of the mesh.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RadioConfig:
    freq: float
    bw: float
    sf: int
    cr: int


# Ottawa mesh preset.
RADIO = RadioConfig(freq=910.525, bw=62.5, sf=7, cr=5)
