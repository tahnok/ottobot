"""Links into MeshCore Beacon, the packet analyzer the Ottawa mesh reports to.

Beacon (https://github.com/MeshCore-Beacon/beacon-server) identifies a
packet by a hash of its payload — the same hash the MeshCore firmware uses
to deduplicate floods. The path is not hashed, so every relayed copy of a
message has the same hash no matter which repeaters it came through, and a
packet the bot heard can be looked up by computing it locally.
"""

from __future__ import annotations

import hashlib

# Short-link host that redirects /<packet hash> to that packet on the
# Beacon instance the Ottawa mesh's observers feed (analyzer.meshmapper.net).
# A reply has to fit in one mesh packet, and the full Beacon URL takes up
# half of it. The redirect rule is deploy/short-links/Caddyfile.
SHORT_LINK_URL = "https://m.tahnok.ca"

# Beacon (via meshcore-go) keeps the first 8 bytes of the SHA-256 digest.
PACKET_HASH_SIZE = 8

# TRACE packets also hash their path length, which the other types don't.
PAYLOAD_TYPE_TRACE = 9


def packet_hash(payload_type: int, payload: bytes) -> str:
    """Beacon's hex packet hash: SHA-256(payload_type byte + payload)[:8].

    *payload* is the packet body after the header, transport codes and
    path. TRACE packets hash differently and aren't supported.
    """
    if payload_type == PAYLOAD_TYPE_TRACE:
        raise ValueError("TRACE packet hashes also cover the path length")
    digest = hashlib.sha256(bytes([payload_type]) + payload).digest()
    return digest[:PACKET_HASH_SIZE].hex()


def packet_url(hash_hex: str) -> str:
    """A short link to the Beacon page analyzing the packet with *hash_hex*."""
    return f"{SHORT_LINK_URL}/{hash_hex}"
