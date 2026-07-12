"""Tests for auto-discovery of command modules."""

import importlib
import types

import pytest

from ottobot import OttoBot
from ottobot.cli import build_bot
from ottobot.commands import iter_command_module_names, load_commands
from ottobot.registry import module_commands


def test_every_command_module_defines_a_command() -> None:
    for name in iter_command_module_names():
        module = importlib.import_module(f"ottobot.commands.{name}")
        assert module_commands(
            module
        ), f"{module.__name__} must define at least one @command handler"


def test_load_commands_loads_all_modules() -> None:
    loaded = load_commands(OttoBot(name="ottobot"))
    assert loaded == iter_command_module_names()
    assert {"ping", "echo", "roll"} <= set(loaded)


def test_no_name_collisions_across_command_files() -> None:
    # CommandRegistry raises ValueError on duplicates; a clean load proves
    # no two files claim the same command name or alias.
    load_commands(OttoBot(name="ottobot"))


def test_build_bot_exposes_all_commands() -> None:
    bot = build_bot(name="ottobot")
    for name in ("help", "ping", "echo", "roll", "dice"):
        assert bot.registry.get(name) is not None


def test_module_without_commands_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ottobot.commands as commands_pkg

    monkeypatch.setattr(commands_pkg, "iter_command_module_names", lambda: ["broken"])
    monkeypatch.setattr(
        commands_pkg.importlib, "import_module", lambda name: types.ModuleType(name)
    )
    with pytest.raises(TypeError, match="must define at least one @command"):
        load_commands(OttoBot(name="ottobot"))


def test_imported_handlers_are_not_re_registered() -> None:
    # A module that imports another command's handler must not register
    # it a second time — module_commands only picks up handlers defined
    # in the module itself.
    from ottobot.commands import ping

    impostor = types.ModuleType("impostor")
    setattr(impostor, "ping", ping.ping)
    assert module_commands(impostor) == []
