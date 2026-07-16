"""Bridges an Ottobot to a real MeshCore device via the meshcore library.

The runner subscribes to channel message events, normalizes them into
IncomingMessage objects, and routes replies back through the device on the
same channel.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Protocol

from meshcore import EventType, MeshCore
from meshcore.events import Event, Subscription

from .bot import Ottobot
from .channels import PUBLIC, ChannelConfig, channel_for_index
from .config import BotConfig
from .radio import RADIO
from .context import IncomingMessage, TaskContext
from .registry import ScheduledTask

logger = logging.getLogger(__name__)

# Probed when the device doesn't report its own max_channels (older firmware).
DEFAULT_MAX_CHANNELS = 8

# MeshCore's built-in "public" channel uses this fixed, network-wide key
# (base64 izOH6cXN6mrJ5e26oRXNcg==), NOT one derived from its name. The
# meshcore library's set_channel() falls back to sha256(name)[:16] for any
# secret-less channel, which is right for "#hashtag" channels but wrong for
# "public" — a device keyed that way silently fails to decrypt all public
# traffic. We substitute the real key here so a secret-less "public" works.
PUBLIC_CHANNEL_KEY = bytes.fromhex("8b3387e9c5cdea6ac9e5edbaa115cd72")

# Path hash mode value for 2-byte-per-hop path hashes (mode N means N+1
# bytes per hop: 0=1 byte, 1=2 bytes, ...; see IncomingMessage.path_hash_size).
# Keeps message overhead low while still avoiding the 1-byte mode's collision
# rate on busier meshes.
PATH_HASH_MODE_2_BYTE = 1

# Minimum gap between two channel transmissions, in seconds. send_chan_msg
# returns as soon as the firmware has *queued* a message, not when the radio
# has finished putting it on the air — which takes up to ~0.5 s at the
# configured preset (SF7/BW62.5). Firing the next send right away makes the
# two collide and one is silently lost (seen when a task announces several
# alerts at once), so sends are serialized and held this far apart.
SEND_SPACING_SECONDS = 2.0


class _Commands(Protocol):
    """The ``mc.commands`` methods the runner calls."""

    async def get_channel(self, channel_idx: int) -> Event: ...
    async def set_name(self, name: str) -> Event: ...
    async def import_private_key(self, key: bytes) -> Event: ...
    async def set_channel(
        self, channel_idx: int, channel_name: str, channel_secret: Any = ...
    ) -> Event: ...
    async def set_radio(self, freq: float, bw: float, sf: int, cr: int) -> Event: ...
    async def set_path_hash_mode(self, mode: int) -> Event: ...
    async def send_chan_msg(self, channel_idx: int, text: str) -> Event: ...


class MeshCoreLike(Protocol):
    """The slice of ``meshcore.MeshCore`` the runner depends on.

    Both the real ``MeshCore`` and the tests' ``FakeMeshCore`` satisfy this,
    so the runner is typed without naming the concrete class (and without the
    permissive ``Any`` that hid mistakes at the call sites below).
    """

    @property
    def commands(self) -> _Commands: ...

    @property
    def self_info(self) -> dict[str, Any] | None: ...

    def subscribe(
        self, event_type: EventType, callback: Callable[[Event], Any]
    ) -> Any: ...
    def unsubscribe(self, subscription: Any) -> None: ...
    def set_decrypt_channel_logs(self, v: bool) -> None: ...
    async def ensure_contacts(self) -> bool: ...
    async def start_auto_message_fetching(self) -> Any: ...
    async def stop_auto_message_fetching(self) -> Any: ...


async def fetch_channels(mc: MeshCoreLike) -> list[dict[str, Any]]:
    """Read the channels actually configured on the device.

    Probes each channel slot (up to the device's reported ``max_channels``,
    or ``DEFAULT_MAX_CHANNELS`` if it doesn't say) and returns the
    CHANNEL_INFO payloads for the populated ones. Slots with a blank name
    are unconfigured and skipped.
    """
    max_channels = (mc.self_info or {}).get("max_channels") or DEFAULT_MAX_CHANNELS
    channels: list[dict[str, Any]] = []
    for idx in range(max_channels):
        result = await mc.commands.get_channel(idx)
        if result.type == EventType.ERROR:
            logger.debug("get_channel(%d) failed: %r", idx, result.payload)
            continue
        payload = result.payload
        if not payload.get("channel_name"):
            continue
        channels.append(payload)
    return channels


def _apply(description: str, result: Event) -> None:
    """Log the outcome of one device-setting command."""
    if result.type == EventType.ERROR:
        logger.warning("failed to %s: %r", description, result.payload)
    else:
        logger.info("applied %s", description)


async def apply_settings(mc: MeshCoreLike, config: BotConfig) -> None:
    """Push the config's name and key pair, plus the shared channels and radio,
    onto the device.

    The config fields (name, private key) are optional; anything left unset is
    skipped so the device keeps its current value. The channels, radio preset,
    and path hash mode are hardcoded (see ``ottobot.channels`` /
    ``ottobot.radio``) and always applied so every Ottawa bot matches.
    """
    _apply(
        "path hash mode=2-byte",
        await mc.commands.set_path_hash_mode(PATH_HASH_MODE_2_BYTE),
    )
    if config.name:
        _apply(f"name={config.name!r}", await mc.commands.set_name(config.name))
    if config.private_key is not None:
        _apply("private key", await mc.commands.import_private_key(config.private_key))
    for channel in config.channels:
        secret = channel.secret
        if secret is None and channel.name.lower() == PUBLIC.name:
            secret = PUBLIC_CHANNEL_KEY
        _apply(
            f"channel {channel.index} name={channel.name!r}",
            await mc.commands.set_channel(channel.index, channel.name, secret),
        )
    _apply(
        f"radio freq={RADIO.freq} bw={RADIO.bw} sf={RADIO.sf} cr={RADIO.cr}",
        await mc.commands.set_radio(RADIO.freq, RADIO.bw, RADIO.sf, RADIO.cr),
    )


async def connect(
    serial: str | None = None,
    baudrate: int = 115200,
    ble: str | None = None,
    tcp: str | None = None,
) -> MeshCore:
    """Connect to a MeshCore companion device over exactly one transport.

    tcp is given as "host:port".
    """
    given = [t for t in (serial, ble, tcp) if t]
    if len(given) != 1:
        raise ValueError("specify exactly one of serial, ble, or tcp")
    if serial:
        return await MeshCore.create_serial(serial, baudrate)
    if ble:
        return await MeshCore.create_ble(ble)
    assert tcp is not None
    host, _, port = tcp.partition(":")
    return await MeshCore.create_tcp(host, int(port or 5000))


class MeshCoreRunner:
    """Runs an Ottobot against a connected meshcore.MeshCore instance."""

    def __init__(self, bot: Ottobot, meshcore: MeshCoreLike) -> None:
        self.bot = bot
        self.mc = meshcore
        self._subscriptions: list[Subscription] = []
        self._scheduled_tasks: list[asyncio.Task[None]] = []
        # Serializes device transmissions and tracks when the last one went
        # out, so back-to-back sends are spaced (see SEND_SPACING_SECONDS).
        self._send_lock = asyncio.Lock()
        self._last_send_at: float | None = None

    async def start(self) -> None:
        """Subscribe to message events and start fetching from the device."""
        await self.mc.ensure_contacts()
        # Lets the library recover the real repeater path for channel
        # messages (by matching against logged raw packets) instead of just
        # a hop count. No effect on DMs, and a no-op if the device never
        # forwards raw packet logs.
        self.mc.set_decrypt_channel_logs(True)
        self._subscriptions = [
            self.mc.subscribe(EventType.CHANNEL_MSG_RECV, self._on_channel_msg),
        ]
        # Without this, incoming messages stay queued on the device and
        # the *_MSG_RECV events never fire.
        await self.mc.start_auto_message_fetching()
        await self._log_channels()
        self._scheduled_tasks = [
            asyncio.create_task(self._run_scheduled_task(scheduled))
            for scheduled in self.bot.tasks
        ]
        logger.info("bot started as %r, listening for messages", self.bot.name)

    async def _log_channels(self) -> None:
        """Log the channels the device is configured for, read from the radio."""
        try:
            channels = await fetch_channels(self.mc)
        except Exception:
            logger.exception("could not read channels from the device")
            return
        if not channels:
            logger.info("no channels configured on the device")
            return
        summary = ", ".join(
            f"{c['channel_idx']}:{c['channel_name']} (#{c['channel_hash']})"
            for c in channels
        )
        logger.info("device channels: %s", summary)

    async def stop(self) -> None:
        for subscription in self._subscriptions:
            self.mc.unsubscribe(subscription)
        self._subscriptions = []
        for scheduled_task in self._scheduled_tasks:
            scheduled_task.cancel()
        self._scheduled_tasks = []
        await self.mc.stop_auto_message_fetching()

    async def _run_scheduled_task(self, scheduled: ScheduledTask) -> None:
        """Run *scheduled* immediately, then again every scheduled.interval, forever."""
        while True:
            await self._run_task_once(scheduled)
            await asyncio.sleep(scheduled.interval.total_seconds())

    async def _run_task_once(self, scheduled: ScheduledTask) -> None:
        async def broadcast(text: str) -> None:
            await self._broadcast(text, scheduled.channel)

        ctx = TaskContext(_reply=broadcast, config=self.bot.config)
        try:
            result = await scheduled.handler(ctx)
        except Exception:
            logger.exception("scheduled task %r raised", scheduled.name)
            return
        if result is not None:
            await broadcast(result)

    async def _send_chan_msg(self, channel_idx: int, text: str) -> Event:
        """Transmit one channel message, serialized and spaced from the last.

        send_chan_msg's OK only means the firmware queued the message, not
        that the radio finished transmitting it. Two sends fired back-to-back
        collide on the air and one is silently dropped, so hold a lock and
        wait out the rest of SEND_SPACING_SECONDS since the previous send
        before starting the next.
        """
        async with self._send_lock:
            if self._last_send_at is not None:
                elapsed = asyncio.get_running_loop().time() - self._last_send_at
                remaining = SEND_SPACING_SECONDS - elapsed
                if remaining > 0:
                    await asyncio.sleep(remaining)
            try:
                return await self.mc.commands.send_chan_msg(channel_idx, text)
            finally:
                self._last_send_at = asyncio.get_running_loop().time()

    async def _broadcast(self, text: str, channel: ChannelConfig) -> None:
        """Send *text* on *channel*.

        Scheduled tasks have no inbound message to reply to, so their output
        goes to their declared channel instead. A channel the bot hasn't
        joined drops the message rather than posting to an unconfigured slot.
        """
        if channel not in self.bot.config.channels:
            logger.error(
                "channel %s is not joined; dropping broadcast %r",
                channel.name,
                text,
            )
            return
        logger.info("broadcast to %s (idx %d): %r", channel.name, channel.index, text)
        result = await self._send_chan_msg(channel.index, text)
        if result.type == EventType.ERROR:
            logger.error("failed to broadcast to %s: %r", channel.name, result.payload)

    async def run_forever(self) -> None:
        """Start the bot and block until cancelled."""
        await self.start()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await self.stop()

    async def _on_channel_msg(self, event: Event) -> None:
        payload = event.payload
        channel_idx = payload.get("channel_idx", 0)
        # Channel messages carry no sender key; by MeshCore convention the
        # sender's name is embedded as a "Name: message" prefix in the text.
        raw_text = payload.get("text", "")
        sender_name, sep, body = raw_text.partition(":")
        if sep:
            sender_name = sender_name.strip()
            text = body.strip()
        else:
            sender_name = None
            text = raw_text
        message = IncomingMessage(
            text=text,
            sender_name=sender_name,
            channel_idx=channel_idx,
            path_len=payload.get("path_len"),
            path=payload.get("path"),
            path_hash_mode=payload.get("path_hash_mode"),
            raw=payload,
        )

        channel = channel_for_index(channel_idx)
        label = channel.name if channel else f"channel {channel_idx}"

        logger.info(
            "%s msg from %s [%s]: %r",
            label,
            sender_name,
            message.path_description,
            text,
        )

        async def reply(text: str) -> None:
            logger.info("%s reply: %r", label, text)
            result = await self._send_chan_msg(channel_idx, text)
            if result.type == EventType.ERROR:
                logger.error("failed to send channel reply: %r", result.payload)

        await self.bot.dispatch(message, reply)
