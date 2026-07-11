"""The MeshCore channels Ottobot tunes its device to — the source of truth.

These are the channel slots the bot joins, the same for every Ottawa bot, so
they live in code rather than per-bot TOML config: ``runner.apply_settings``
pushes them onto the device on startup, and scheduled tasks (e.g.
``weather_alerts``, which posts to ``#ott-alerts``) look their channel up
here by name. The device only sends/receives on channels it has a slot for,
so a channel must be listed here to be heard or posted to.

This is *not* the user-facing directory of Ottawa channels that ``!channels``
prints — that list (in ``commands/channels.py``) is informational and much
longer; the bot doesn't join every channel it tells users about.

``index`` is the device channel slot. ``name`` is how the channel is
addressed; a ``None`` ``secret`` lets the device derive the key from the name
(the MeshCore default for ``#``-prefixed names). ``"public"`` is the MeshCore
default public channel, which ``runner`` keys with the well-known public key.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelConfig:
    index: int
    name: str
    # 16-byte secret; None means the device derives it from the name.
    secret: bytes | None = None


# Named so code can refer to a channel directly (e.g. ``@task(channel=OTT_ALERTS)``
# or ``PUBLIC.index``) instead of by a bare string name or magic index.
PUBLIC = ChannelConfig(0, "public")
TESTING = ChannelConfig(1, "#testing")
OTTOBOT_TESTING = ChannelConfig(2, "#ottobot-testing")
# Joined so the weather_alerts task can broadcast here — you can't post to a
# channel the device hasn't joined.
OTT_ALERTS = ChannelConfig(3, "#ott-alerts")

CHANNELS: tuple[ChannelConfig, ...] = (PUBLIC, TESTING, OTTOBOT_TESTING, OTT_ALERTS)


def channel_for_index(index: int) -> ChannelConfig | None:
    """The joined channel occupying device slot *index*, or None if unused."""
    for channel in CHANNELS:
        if channel.index == index:
            return channel
    return None
