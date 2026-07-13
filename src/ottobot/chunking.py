"""Splitting long replies into mesh-sized chunks.

MeshCore drops (truncates) a channel message past a fixed byte limit, so a
long reply — like a growing ``!help`` listing — silently loses its tail. This
module provides the pure ``chunk_message`` helper and the ``MAX_MESSAGE_LEN``
default; sending is opt-in via ``Context.reply_chunks`` so ordinary short
replies keep going out as a single packet.
"""

from __future__ import annotations

# MeshCore channel messages are capped at ~140 characters; anything longer is
# truncated on the wire. Kept a touch conservative to leave room for the
# "Name: " sender prefix convention.
MAX_MESSAGE_LEN = 140


def _split_line(line: str, limit: int) -> list[str]:
    """Break a single line into pieces of at most *limit* characters.

    Splits on spaces so words stay intact; a single word longer than the
    limit is hard-split at the limit as a last resort.
    """
    if len(line) <= limit:
        return [line]
    pieces: list[str] = []
    current = ""
    for word in line.split(" "):
        while len(word) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(word[:limit])
            word = word[limit:]
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    return pieces


def chunk_message(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Split *text* into chunks each at most *limit* characters.

    Whole lines are kept together and packed greedily, so a multi-line
    listing breaks between its entries rather than mid-line. A line longer
    than the limit is split on whitespace (and, only if a single word still
    overflows, hard-split). Empty or whitespace-only input yields ``[]`` so
    callers send nothing.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not text.strip():
        return []
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        for piece in _split_line(line, limit):
            candidate = f"{current}\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks
