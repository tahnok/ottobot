from hypothesis import given
from hypothesis import strategies as st

from ottobot.messages import MAX_MESSAGE_LEN, fits, pack_messages


def test_fits_counts_utf8_bytes() -> None:
    assert fits("x" * MAX_MESSAGE_LEN)
    assert not fits("x" * (MAX_MESSAGE_LEN + 1))
    # "é" is two bytes, so half as many fit.
    assert fits("é" * (MAX_MESSAGE_LEN // 2))
    assert not fits("é" * (MAX_MESSAGE_LEN // 2 + 1))


def test_fits_does_not_raise_on_a_lone_surrogate() -> None:
    assert fits("\ud800")
    assert not fits("\ud800" * MAX_MESSAGE_LEN)


def test_pack_nothing() -> None:
    assert pack_messages([]) == []


def test_pack_short_list_into_one_message() -> None:
    assert pack_messages(["Channels:", "#a", "#b"]) == ["Channels: #a #b"]


def test_pack_starts_a_new_message_when_the_next_part_wont_fit() -> None:
    assert pack_messages(["aaaa", "bbbb", "cccc"], limit=9) == ["aaaa bbbb", "cccc"]


def test_pack_uses_the_separator() -> None:
    assert pack_messages(["a", "b"], sep="\n") == ["a\nb"]


def test_pack_sends_an_oversized_part_alone() -> None:
    assert pack_messages(["a", "x" * 20, "b"], limit=10) == ["a", "x" * 20, "b"]


@given(
    st.lists(st.text(alphabet=st.characters(exclude_characters=" "), max_size=60)),
    st.integers(min_value=1, max_value=200),
)
def test_pack_loses_nothing_and_respects_the_limit(
    parts: list[str], limit: int
) -> None:
    messages = pack_messages(parts, limit=limit)
    assert " ".join(messages) == " ".join(parts)
    for message in messages:
        assert fits(message, limit) or message in parts
