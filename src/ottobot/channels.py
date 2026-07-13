"""The MeshCore channels Ottobot tunes its device to — the source of truth.

These are the channel slots the bot joins, the same for every Ottawa bot, so
they live in code rather than per-bot TOML config: ``runner.apply_settings``
pushes them onto the device on startup, and scheduled tasks (e.g.
``weather_alerts``) reference the channel constants here directly. The device
only sends/receives on channels it has a slot for, so a channel must be
listed here to be heard or posted to.

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


PUBLIC = ChannelConfig(0, "public")
TESTING = ChannelConfig(1, "#testing")
OTTOBOT_TESTING = ChannelConfig(2, "#ottobot-testing")
OTT_ALERTS = ChannelConfig(3, "#ott-alerts")
BOTS = ChannelConfig(4, "#bots")

CHANNELS: tuple[ChannelConfig, ...] = (
    PUBLIC,
    TESTING,
    OTTOBOT_TESTING,
    OTT_ALERTS,
    BOTS,
)

# Where the bot answers commands. It still *hears* every channel it has
# joined (sinks see everything, e.g. the public-channel welcome), but
# command replies are kept off the public and alert channels so the bot
# doesn't add chatter there — conversations happen on #bots.
COMMAND_CHANNELS: tuple[ChannelConfig, ...] = (BOTS, TESTING, OTTOBOT_TESTING)


def channel_for_index(index: int) -> ChannelConfig | None:
    """The joined channel occupying device slot *index*, or None if unused."""
    for channel in CHANNELS:
        if channel.index == index:
            return channel
    return None
