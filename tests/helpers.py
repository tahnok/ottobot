"""Shared test helpers for building messages and recording replies."""

from typing import Any

from ottawa_meshbot.context import IncomingMessage


def dm(text: str, **extra: Any) -> IncomingMessage:
    return IncomingMessage(
        text=text, sender_key="abcd1234", sender_name="alice", **extra
    )


def channel_msg(text: str, idx: int = 0, **extra: Any) -> IncomingMessage:
    return IncomingMessage(text=text, sender_name="alice", channel_idx=idx, **extra)


class ReplyRecorder:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def __call__(self, text: str) -> None:
        self.replies.append(text)
