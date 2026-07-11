"""Shared test helpers for building messages and recording replies."""

from typing import Any

from ottobot.context import IncomingMessage


def channel_msg(text: str, idx: int = 0, **extra: Any) -> IncomingMessage:
    return IncomingMessage(text=text, sender_name="alice", channel_idx=idx, **extra)


def addressed(text: str, idx: int = 0, **extra: Any) -> IncomingMessage:
    """A channel message that addresses the bot by name, so commands run."""
    return channel_msg(f"@[ottobot] {text}", idx=idx, **extra)


class ReplyRecorder:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def __call__(self, text: str) -> None:
        self.replies.append(text)
