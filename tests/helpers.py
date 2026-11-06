"""Shared test helpers for building messages and recording replies."""

from typing import Any

from meshcore import EventType
from meshcore.events import Event

from ottobot.channels import BOTS
from ottobot.context import IncomingMessage


def channel_msg(text: str, idx: int = 0, **extra: Any) -> IncomingMessage:
    return IncomingMessage(text=text, sender_name="alice", channel_idx=idx, **extra)


def addressed(text: str, idx: int = BOTS.index, **extra: Any) -> IncomingMessage:
    """A channel message that addresses the bot by name, on a command
    channel (#bots by default), so commands run."""
    return channel_msg(f"@[ottobot] {text}", idx=idx, **extra)


class ReplyRecorder:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def __call__(self, text: str) -> None:
        self.replies.append(text)


class FakeEvent(Event):
    def __init__(self, type: EventType, payload: Any = None) -> None:
        super().__init__(type, payload)


class FakeCommands:
    def __init__(self) -> None:
        self.sent_chan_msgs: list[tuple[int, str]] = []
        # The sender timestamp each sent message was stamped with.
        self.sent_timestamps: list[int | None] = []
        # Records device-setting calls for apply_settings tests.
        self.names: list[str] = []
        self.private_keys: list[bytes] = []
        self.channels: list[tuple[int, str, bytes | None]] = []
        self.radios: list[tuple[float, float, int, int]] = []
        self.path_hash_modes: list[int] = []
        # Channels the fake device reports back via get_channel, keyed by
        # index; each value is the CHANNEL_INFO-style name. Empty by default.
        self.device_channels: dict[int, str] = {}

    async def get_channel(self, channel_idx: int) -> FakeEvent:
        name = self.device_channels.get(channel_idx, "")
        return FakeEvent(
            EventType.CHANNEL_INFO,
            {
                "channel_idx": channel_idx,
                "channel_name": name,
                "channel_secret": b"\x00" * 16,
                "channel_hash": f"{channel_idx:02x}",
            },
        )

    async def send_chan_msg(
        self, channel_idx: int, text: str, timestamp: int | None = None
    ) -> FakeEvent:
        self.sent_chan_msgs.append((channel_idx, text))
        self.sent_timestamps.append(timestamp)
        return FakeEvent(EventType.MSG_SENT, {"expected_ack": b"\x00"})

    async def set_name(self, name: str) -> FakeEvent:
        self.names.append(name)
        return FakeEvent(EventType.OK)

    async def import_private_key(self, key: bytes) -> FakeEvent:
        self.private_keys.append(key)
        return FakeEvent(EventType.OK)

    async def set_channel(
        self, channel_idx: int, channel_name: str, channel_secret: bytes | None = None
    ) -> FakeEvent:
        self.channels.append((channel_idx, channel_name, channel_secret))
        return FakeEvent(EventType.OK)

    async def set_radio(self, freq: float, bw: float, sf: int, cr: int) -> FakeEvent:
        self.radios.append((freq, bw, sf, cr))
        return FakeEvent(EventType.OK)

    async def set_path_hash_mode(self, mode: int) -> FakeEvent:
        self.path_hash_modes.append(mode)
        return FakeEvent(EventType.OK)


class FakeMeshCore:
    def __init__(
        self,
        self_info: dict[str, Any] | None = None,
    ) -> None:
        self.commands = FakeCommands()
        self.callbacks: dict[EventType, Any] = {}
        self.self_info: dict[str, Any] | None = (
            self_info
            if self_info is not None
            else {"name": "ottobot", "max_channels": 4}
        )
        self.ensure_contacts_calls = 0
        self.fetching = False
        self.decrypt_channel_logs = False
        self.disconnect_calls = 0

    def subscribe(self, event_type: EventType, callback: Any) -> tuple[EventType, Any]:
        self.callbacks[event_type] = callback
        return (event_type, callback)

    def set_decrypt_channel_logs(self, v: bool) -> None:
        self.decrypt_channel_logs = v

    def unsubscribe(self, subscription: tuple[EventType, Any]) -> None:
        self.callbacks.pop(subscription[0], None)

    async def ensure_contacts(self) -> bool:
        self.ensure_contacts_calls += 1
        return True

    async def start_auto_message_fetching(self) -> None:
        self.fetching = True

    async def stop_auto_message_fetching(self) -> None:
        self.fetching = False

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def deliver_chan(
        self,
        text: str,
        channel_idx: int = 0,
        path_len: int | None = None,
        path: str | None = None,
        path_hash_mode: int | None = None,
        **extra: Any,
    ) -> None:
        payload: dict[str, Any] = {
            "type": "CHAN",
            "channel_idx": channel_idx,
            "text": text,
            **extra,
        }
        if path_len is not None:
            payload["path_len"] = path_len
        if path is not None:
            payload["path"] = path
        if path_hash_mode is not None:
            payload["path_hash_mode"] = path_hash_mode
        event = FakeEvent(EventType.CHANNEL_MSG_RECV, payload)
        await self.callbacks[EventType.CHANNEL_MSG_RECV](event)

    async def deliver_rx_log(self, **payload: Any) -> None:
        event = FakeEvent(EventType.RX_LOG_DATA, payload)
        await self.callbacks[EventType.RX_LOG_DATA](event)
