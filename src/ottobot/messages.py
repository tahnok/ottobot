"""Fitting replies into mesh packets.

MeshCore truncates a channel message past ~140 UTF-8 bytes (bytes, not
characters — names like "Orléans" aren't pure ASCII). Anything that might
run longer should be split with ``pack_messages`` and sent with
``ctx.reply_many(...)`` rather than returned as one clipped string.
"""

from collections.abc import Iterable

MAX_MESSAGE_LEN = 140


def fits(text: str, limit: int = MAX_MESSAGE_LEN) -> bool:
    """Whether *text* fits in one mesh packet."""
    # surrogatepass: a lone surrogate (which can't be encoded as UTF-8) is
    # measured rather than raising, so a stray one in a help string or
    # incoming text can't crash the handler that's sizing a reply.
    return len(text.encode("utf-8", errors="surrogatepass")) <= limit


def pack_messages(
    parts: Iterable[str], sep: str = " ", limit: int = MAX_MESSAGE_LEN
) -> list[str]:
    """Join *parts* with *sep* into as few packet-sized messages as possible.

    Parts are never split: a new message starts once the next part wouldn't
    fit, and a part too long for a packet on its own goes out alone. To give
    the listing a heading, pass it as the first part, e.g.
    ``pack_messages(["Channels:", *names])``.
    """
    messages: list[str] = []
    current: str | None = None
    for part in parts:
        candidate = part if current is None else f"{current}{sep}{part}"
        if current is None or fits(candidate, limit):
            current = candidate
        else:
            messages.append(current)
            current = part
    if current is not None:
        messages.append(current)
    return messages
