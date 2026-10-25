"""Contract tests: the real meshcore library still matches runner.py's protocols.

The runner tests drive a FakeMeshCore, so they keep passing even if a
meshcore upgrade renames, removes, or reshapes a method the runner calls.
These tests check the protocols in runner.py (which the fake is typed
against) against the installed library instead, so such a break fails CI
rather than the bot on the radio.

The runner calls every method positionally, so parameter names aren't
compared, only that the protocol's calls still bind.
"""

import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest
from meshcore import MeshCore

from ottobot.runner import MeshCoreLike, _Commands


def protocol_members(protocol: type) -> list[str]:
    """The methods and properties *protocol* declares (not inherited ones)."""
    return [
        name
        for name, value in vars(protocol).items()
        if not name.startswith("_")
        and (inspect.isfunction(value) or isinstance(value, property))
    ]


@pytest.fixture(scope="module")
def mc() -> MeshCore:
    # A real MeshCore, so instance attributes like ``commands`` exist too.
    # Constructing one doesn't touch the connection beyond wiring it up.
    return MeshCore(MagicMock())


def assert_conforms(protocol: type, real: Any, name: str) -> None:
    declared = vars(protocol)[name]
    assert hasattr(real, name), f"{type(real).__name__} has no {name!r}"
    if isinstance(declared, property):
        return
    actual = getattr(real, name)
    assert callable(actual), f"{name!r} is no longer callable"
    assert inspect.iscoroutinefunction(actual) == inspect.iscoroutinefunction(
        declared
    ), f"{name!r} changed between sync and async"

    params = list(inspect.signature(declared).parameters.values())[1:]  # self
    positional = [
        p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    required = [p for p in positional if p.default is p.empty]
    real_sig = inspect.signature(actual)
    # Both the shortest and the longest call the protocol allows must bind.
    for count in {len(required), len(positional)}:
        try:
            real_sig.bind(*[object()] * count)
        except TypeError as e:
            pytest.fail(f"{name}{real_sig} no longer accepts {count} args: {e}")


@pytest.mark.parametrize("name", protocol_members(MeshCoreLike))
def test_meshcore_matches_protocol(mc: MeshCore, name: str) -> None:
    assert_conforms(MeshCoreLike, mc, name)


@pytest.mark.parametrize("name", protocol_members(_Commands))
def test_meshcore_commands_match_protocol(mc: MeshCore, name: str) -> None:
    assert_conforms(_Commands, mc.commands, name)


def test_protocols_are_not_empty() -> None:
    # Guards the parametrized tests above against silently collecting nothing.
    assert "subscribe" in protocol_members(MeshCoreLike)
    assert "commands" in protocol_members(MeshCoreLike)
    assert "send_chan_msg" in protocol_members(_Commands)
