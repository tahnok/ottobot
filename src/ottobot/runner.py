"""Bridges a MeshBot to a real MeshCore device via the meshcore library.

The runner subscribes to direct and channel message events, normalizes
them into IncomingMessage objects, and routes replies back through the
device (DM replies to the sending contact, channel replies to the same
channel).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from meshcore import EventType, MeshCore

from .bot import MeshBot
from .context import IncomingMessage

logger = logging.getLogger(__name__)


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
    """Runs a MeshBot against a connected meshcore.MeshCore instance."""

    def __init__(self, bot: MeshBot, meshcore: Any) -> None:
        self.bot = bot
        self.mc = meshcore
        self._subscriptions: list[Any] = []

    async def start(self) -> None:
        """Subscribe to message events and start fetching from the device."""
        await self.mc.ensure_contacts()
        self._subscriptions = [
            self.mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_contact_msg),
            self.mc.subscribe(EventType.CHANNEL_MSG_RECV, self._on_channel_msg),
        ]
        # Without this, incoming messages stay queued on the device and
        # the *_MSG_RECV events never fire.
        await self.mc.start_auto_message_fetching()
        logger.info("bot started, listening for messages")

    async def stop(self) -> None:
        for subscription in self._subscriptions:
            self.mc.unsubscribe(subscription)
        self._subscriptions = []
        await self.mc.stop_auto_message_fetching()

    async def run_forever(self) -> None:
        """Start the bot and block until cancelled."""
        await self.start()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await self.stop()

    async def _on_contact_msg(self, event: Any) -> None:
        payload = event.payload
        prefix = payload.get("pubkey_prefix")
        contact = await self._resolve_contact(prefix)
        if contact is None:
            logger.warning("DM from unknown contact %r, cannot reply; ignoring", prefix)
            return
        message = IncomingMessage(
            text=payload.get("text", ""),
            sender_key=prefix,
            sender_name=contact.get("adv_name"),
            path_len=payload.get("path_len"),
            path=payload.get("path"),
            path_hash_mode=payload.get("path_hash_mode"),
            raw=payload,
        )

        async def reply(text: str) -> None:
            result = await self.mc.commands.send_msg(contact, text)
            if result.type == EventType.ERROR:
                logger.error("failed to send DM reply: %r", result.payload)

        await self.bot.dispatch(message, reply)

    async def _on_channel_msg(self, event: Any) -> None:
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

        async def reply(text: str) -> None:
            result = await self.mc.commands.send_chan_msg(channel_idx, text)
            if result.type == EventType.ERROR:
                logger.error("failed to send channel reply: %r", result.payload)

        await self.bot.dispatch(message, reply)

    async def _resolve_contact(self, prefix: str | None) -> dict[str, Any] | None:
        if not prefix:
            return None
        contact: dict[str, Any] | None = self.mc.get_contact_by_key_prefix(prefix)
        if contact is None:
            # Maybe a contact added since startup; refresh the list once.
            await self.mc.ensure_contacts()
            contact = self.mc.get_contact_by_key_prefix(prefix)
        return contact
