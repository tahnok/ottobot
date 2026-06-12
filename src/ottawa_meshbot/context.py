"""Incoming message model and the context passed to command handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IncomingMessage:
    """A message received from the mesh, normalized away from transport details.

    channel_idx is None for direct messages and the channel number for
    channel/group messages. sender_key is the sender's public key prefix
    (hex) for direct messages; channel messages may not carry one.

    path_len is the raw value reported by the device: 255 means the message
    arrived directly (zero hops), otherwise it is the number of repeater
    hops. path, when known, is a hex string of repeater node hashes,
    outermost repeater first. path_hash_mode sets the hash width: mode N
    means N+1 bytes per hop (mode 0 = legacy 1-byte hashes, mode 2 =
    3-byte hashes); -1 is reported for direct messages.

    raw is the unmodified event payload from the transport — for meshcore,
    the *_MSG_RECV payload dict (SNR, sender_timestamp, txt_type, ...).
    It is an escape hatch for transport data the framework doesn't model;
    None when the message was built without a transport (e.g. in tests).
    """

    text: str
    sender_key: str | None = None
    sender_name: str | None = None
    channel_idx: int | None = None
    path_len: int | None = None
    path: str | None = None
    path_hash_mode: int | None = None
    raw: dict[str, Any] | None = None

    @property
    def is_dm(self) -> bool:
        return self.channel_idx is None

    @property
    def hop_count(self) -> int | None:
        """Number of repeater hops, 0 if received directly, None if unknown."""
        if self.path_len is None:
            return None
        return 0 if self.path_len == 255 else self.path_len

    @property
    def path_hash_size(self) -> int:
        """Bytes per repeater hash in path (hash mode N means N+1 bytes)."""
        if self.path_hash_mode is not None and self.path_hash_mode >= 0:
            return self.path_hash_mode + 1
        return 1

    @property
    def path_description(self) -> str:
        """Human-readable route the message took, e.g. "direct" or "2 hops via a1,b2"."""
        hops = self.hop_count
        if hops is None:
            return "unknown path"
        if hops == 0:
            return "direct"
        label = "hop" if hops == 1 else "hops"
        if self.path:
            step = self.path_hash_size * 2
            route = ",".join(self.path[i : i + step] for i in range(0, len(self.path), step))
            return f"{hops} {label} via {route}"
        return f"{hops} {label}"


ReplyFunc = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class Context:
    """What a command handler gets to work with."""

    message: IncomingMessage
    command_name: str
    args: str
    _reply: ReplyFunc

    @property
    def is_dm(self) -> bool:
        return self.message.is_dm

    @property
    def sender_name(self) -> str | None:
        return self.message.sender_name

    @property
    def path_description(self) -> str:
        return self.message.path_description

    @property
    def raw(self) -> dict[str, Any] | None:
        """The unmodified transport event payload, if any (escape hatch)."""
        return self.message.raw

    async def reply(self, text: str) -> None:
        """Send text back where the message came from (DM or channel)."""
        await self._reply(text)
