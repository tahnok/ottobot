"""Tests for auto-discovery of command/sink/task modules."""

import importlib
import types

import pytest

import ottobot.commands as commands_pkg
from ottobot import Ottobot
from ottobot import discovery
from ottobot.cli import build_bot
from ottobot.commands import load_commands
from ottobot.discovery import iter_module_names
from ottobot.registry import module_commands
from ottobot.sinks import load_sinks
from ottobot.tasks import load_tasks


def test_every_command_module_defines_a_command() -> None:
    for name in iter_module_names(commands_pkg.__path__):
        module = importlib.import_module(f"ottobot.commands.{name}")
        assert module_commands(
            module
        ), f"{module.__name__} must define at least one @command handler"


def test_load_commands_loads_all_modules() -> None:
    loaded = load_commands(Ottobot(name="ottobot"))
    assert loaded == iter_module_names(commands_pkg.__path__)
    assert {"ping", "echo", "roll"} <= set(loaded)


def test_no_name_collisions_across_command_files() -> None:
    # Ottobot.add_command raises ValueError on duplicates; a clean load
    # proves no two files claim the same command name or alias.
    load_commands(Ottobot(name="ottobot"))


def test_build_bot_exposes_all_commands() -> None:
    bot = build_bot(name="ottobot")
    for name in ("help", "ping", "echo", "roll", "dice"):
        assert bot.get_command(name) is not None


@pytest.mark.parametrize(
    "load, kind",
    [(load_commands, "@command"), (load_sinks, "@sink"), (load_tasks, "@task")],
)
def test_module_without_handlers_is_rejected(
    monkeypatch: pytest.MonkeyPatch, load, kind
) -> None:
    monkeypatch.setattr(discovery, "iter_module_names", lambda path: ["broken"])
    monkeypatch.setattr(
        discovery.importlib, "import_module", lambda name: types.ModuleType(name)
    )
    with pytest.raises(TypeError, match=f"must define at least one {kind}"):
        load(Ottobot(name="ottobot"))


def test_imported_handlers_are_not_re_registered() -> None:
    # A module that imports another command's handler must not register
    # it a second time — module_commands only picks up handlers defined
    # in the module itself.
    from ottobot.commands import ping

    impostor = types.ModuleType("impostor")
    setattr(impostor, "ping", ping.ping)
    assert module_commands(impostor) == []
