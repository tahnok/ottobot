"""Property-based tests (hypothesis) for the bot's pure, transport-free logic.

Each test states a property that must hold for *any* input in a strategy,
and hypothesis searches for a counterexample. They complement the
example-based tests: dispatch, parsing and the reply formatting all take
free text off the mesh, where anyone can send anything.

The bot is built inside each test rather than taken from a fixture, since a
function-scoped fixture would be shared across every generated example.
"""

import hashlib
import re

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from helpers import ReplyRecorder, addressed, channel_msg
from ottobot import Ottobot
from ottobot.beacon import PACKET_HASH_SIZE, PAYLOAD_TYPE_TRACE, packet_hash
from ottobot.bot import MAX_MESSAGE_LEN
from ottobot.channels import COMMAND_CHANNELS
from ottobot.commands import echo, register_module, roll
from ottobot.context import Context, IncomingMessage, split_path
from ottobot.registry import Command

# Command names as people type them: no whitespace, so they survive parsing.
names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1)
# A device channel slot commands are not answered on.
non_command_slots = st.integers(min_value=0, max_value=255).filter(
    lambda idx: all(channel.index != idx for channel in COMMAND_CHANNELS)
)


def make_bot(*modules) -> Ottobot:
    bot = Ottobot(name="ottobot")
    for module in modules:
        register_module(bot, module)
    return bot


async def noop(ctx: Context) -> None:
    return None


# --- parse ------------------------------------------------------------------


@given(st.text())
def test_parse_never_raises_and_yields_a_bare_name(text: str) -> None:
    parsed = Ottobot(name="ottobot").parse(text)
    if parsed is None:
        return
    name, args = parsed
    assert name and " " not in name
    assert not name[0].isspace()
    assert args == args.strip()


@given(st.text())
def test_parse_rejects_text_without_the_prefix(text: str) -> None:
    assume(not text.strip().startswith("!"))
    assert Ottobot(name="ottobot").parse(text) is None


@given(names, st.text())
def test_parse_splits_name_from_args(name: str, args: str) -> None:
    assert Ottobot(name="ottobot").parse(f"!{name} {args}") == (name, args.strip())


@given(st.sampled_from(["!", "/", "?", "bot:"]), names, st.text())
def test_parse_honours_a_custom_prefix(prefix: str, name: str, args: str) -> None:
    bot = Ottobot(name="ottobot", prefix=prefix)
    assert bot.parse(f"  {prefix}{name} {args}") == (name, args.strip())


# --- strip_address ----------------------------------------------------------


@given(st.text())
def test_mention_always_addresses_the_bot(text: str) -> None:
    rest, was_addressed = Ottobot(name="ottobot").strip_address(f"@[ottobot] {text}")
    assert was_addressed
    assert not rest.startswith((" ", ":", ","))
    assert text.rstrip().endswith(rest)


@given(st.text(), st.data())
def test_mention_is_case_insensitive(text: str, data: st.DataObject) -> None:
    name = "".join(
        data.draw(st.sampled_from([c.lower(), c.upper()])) for c in "ottobot"
    )
    _, was_addressed = Ottobot(name="ottobot").strip_address(f"@[{name}] {text}")
    assert was_addressed


@given(
    st.sampled_from(["", "@"]),
    st.sampled_from([" ", ":", ","]),
    st.text(),
)
def test_hand_typed_name_addresses_the_bot(at: str, sep: str, text: str) -> None:
    _, was_addressed = Ottobot(name="ottobot").strip_address(f"{at}ottobot{sep}{text}")
    assert was_addressed


@given(
    st.sampled_from(["", "@"]),
    st.characters(categories=["L", "N"]),
    st.text(),
)
def test_name_as_a_word_prefix_does_not_address_the_bot(
    at: str, letter: str, text: str
) -> None:
    # "ottobotanist" is somebody else.
    rest, was_addressed = Ottobot(name="ottobot").strip_address(
        f"{at}ottobot{letter}{text}"
    )
    assert not was_addressed
    assert rest == f"{at}ottobot{letter}{text}".strip()


@given(st.text())
def test_unaddressed_text_is_only_stripped(text: str) -> None:
    rest, was_addressed = Ottobot(name="ottobot").strip_address(text)
    if not was_addressed:
        assert rest == text.strip()


# --- dispatch ---------------------------------------------------------------


@given(st.text(), st.integers(min_value=0, max_value=255))
async def test_dispatch_never_raises(text: str, idx: int) -> None:
    reply = ReplyRecorder()
    await make_bot(echo, roll).dispatch(channel_msg(text, idx=idx), reply)
    assert all(isinstance(r, str) for r in reply.replies)


@given(st.text(), non_command_slots)
async def test_commands_are_silent_off_the_command_channels(
    text: str, idx: int
) -> None:
    reply = ReplyRecorder()
    await make_bot(echo, roll).dispatch(addressed(text, idx=idx), reply)
    assert reply.replies == []


@given(names, st.text())
async def test_unaddressed_commands_are_ignored(name: str, args: str) -> None:
    reply = ReplyRecorder()
    message = channel_msg(f"!{name} {args}", idx=COMMAND_CHANNELS[0].index)
    await make_bot(echo, roll).dispatch(message, reply)
    assert reply.replies == []


@given(st.text())
async def test_echo_repeats_its_arguments(args: str) -> None:
    reply = ReplyRecorder()
    await make_bot(echo).dispatch(addressed(f"!echo {args}"), reply)
    assert reply.replies == [args.strip() or "(nothing to echo)"]


@given(st.integers(min_value=2, max_value=10**9))
async def test_roll_stays_within_the_die(sides: int) -> None:
    reply = ReplyRecorder()
    await make_bot(roll).dispatch(addressed(f"!roll {sides}"), reply)
    match = re.fullmatch(rf"alice rolled a (\d+) \(d{sides}\)", reply.replies[0])
    assert match
    assert 1 <= int(match.group(1)) <= sides


@given(st.integers(max_value=1))
async def test_roll_rejects_fewer_than_two_sides(sides: int) -> None:
    reply = ReplyRecorder()
    await make_bot(roll).dispatch(addressed(f"!roll {sides}"), reply)
    assert reply.replies == ["A die needs at least 2 sides."]


def _not_an_int(text: str) -> bool:
    try:
        int(text)
    except ValueError:
        return True
    return False


@given(st.text().map(str.strip).filter(bool).filter(_not_an_int))
async def test_roll_rejects_non_numeric_sides(args: str) -> None:
    reply = ReplyRecorder()
    await make_bot(roll).dispatch(addressed(f"!roll {args}"), reply)
    assert reply.replies == ["Usage: !roll [sides]"]


# --- registration and !help -------------------------------------------------


@given(names.filter(lambda n: n != "help"), st.data())
def test_names_are_unique_regardless_of_case(name: str, data: st.DataObject) -> None:
    bot = Ottobot(name="ottobot")
    bot.add_command(Command(name, noop))
    clash = "".join(data.draw(st.sampled_from([c.lower(), c.upper()])) for c in name)
    with pytest.raises(ValueError):
        bot.add_command(Command(clash, noop))


@given(
    st.dictionaries(
        names.filter(lambda n: n != "help"),
        st.text(alphabet=st.characters(exclude_characters="\n"), max_size=200),
        max_size=30,
    )
)
async def test_help_lists_every_command_in_packet_sized_chunks(
    helps: dict[str, str],
) -> None:
    bot = Ottobot(name="ottobot")
    for name, help in helps.items():
        bot.add_command(Command(name, noop, help=help))
    reply = ReplyRecorder()
    await bot.dispatch(addressed("!help"), reply)

    entries = [
        f"!{c.name} - {c.help}" if c.help else f"!{c.name}" for c in bot.commands()
    ]
    # Nothing is lost or reordered across the chunks...
    assert "\n".join(reply.replies) == "\n".join(entries)
    # ...and a chunk only overflows a packet when one entry alone does.
    for chunk in reply.replies:
        assert len(chunk) <= MAX_MESSAGE_LEN or chunk in entries


# --- message paths ----------------------------------------------------------


@given(st.text(alphabet="0123456789abcdef"), st.integers(min_value=1, max_value=4))
def test_split_path_round_trips(path: str, hash_size: int) -> None:
    hops = split_path(path, hash_size)
    assert "".join(hops) == path
    assert all(len(hop) == hash_size * 2 for hop in hops[:-1])
    assert all(0 < len(hop) <= hash_size * 2 for hop in hops)


@given(
    st.integers(min_value=1, max_value=254),
    st.integers(min_value=0, max_value=3),
    st.data(),
)
def test_path_description_names_every_hop(
    hops: int, mode: int, data: st.DataObject
) -> None:
    size = mode + 1
    hashes = data.draw(
        st.lists(
            st.binary(min_size=size, max_size=size).map(bytes.hex),
            min_size=hops,
            max_size=hops,
        )
    )
    message = IncomingMessage(
        text="", path_len=hops, path="".join(hashes), path_hash_mode=mode
    )
    label = "hop" if hops == 1 else "hops"
    assert message.hop_count == hops
    assert message.path_description == f"{hops} {label} via {','.join(hashes)}"


@given(st.sampled_from([0, 255]), st.one_of(st.none(), st.text()))
def test_zero_hops_is_direct(path_len: int, path: str | None) -> None:
    message = IncomingMessage(text="", path_len=path_len, path=path)
    assert message.hop_count == 0
    assert message.path_description == "direct"


# --- beacon -----------------------------------------------------------------


@given(
    st.integers(min_value=0, max_value=255).filter(lambda t: t != PAYLOAD_TYPE_TRACE),
    st.binary(),
)
def test_packet_hash_is_a_truncated_sha256(payload_type: int, payload: bytes) -> None:
    hash_hex = packet_hash(payload_type, payload)
    assert len(hash_hex) == PACKET_HASH_SIZE * 2
    assert (
        hashlib.sha256(bytes([payload_type]) + payload).hexdigest().startswith(hash_hex)
    )


@given(st.binary())
def test_packet_hash_refuses_trace_packets(payload: bytes) -> None:
    with pytest.raises(ValueError):
        packet_hash(PAYLOAD_TYPE_TRACE, payload)
