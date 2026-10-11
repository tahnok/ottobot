import pytest

from ottobot.beacon import packet_hash, packet_url

# A GRP_TXT packet as stored by analyzer.meshmapper.net, with the hash
# Beacon assigned it.
GRP_TXT_PAYLOAD = bytes.fromhex(
    "1f454e35da0c803c2218dad5ec4db4923ae530abffca81d7cb1e0cfa0b23f87b"
    "a21f72494f0c79cd925a218a2abf1681c06e9c12664544b3287a2bce1c182a8d"
    "2f5d08"
)
GRP_TXT_HASH = "90d5b457307ec193"


def test_packet_hash_matches_beacon() -> None:
    assert packet_hash(5, GRP_TXT_PAYLOAD) == GRP_TXT_HASH


def test_packet_hash_covers_payload_type() -> None:
    assert packet_hash(6, GRP_TXT_PAYLOAD) != GRP_TXT_HASH


def test_trace_packets_are_rejected() -> None:
    with pytest.raises(ValueError):
        packet_hash(9, GRP_TXT_PAYLOAD)


def test_packet_url_opens_the_analyzer() -> None:
    assert packet_url(GRP_TXT_HASH) == ("https://m.tahnok.ca/90d5b457307ec193")
