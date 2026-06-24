"""Tests for auto-discovery of command modules."""

import importlib
import types

import pytest

from ottobot import Context, MeshBot, listener
from ottobot.cli import build_bot
from ottobot.commands import (
    iter_command_module_names,
    load_commands,
    register_module,
)
from ottobot.registry import command, module_commands, module_listeners


def test_every_module_defines_a_command_or_listener() -> None:
    for name in iter_command_module_names():
        module = importlib.import_module(f"ottobot.commands.{name}")
        assert module_commands(module) or module_listeners(
            module
        ), f"{module.__name__} must define at least one @command or @listener handler"


def test_load_commands_loads_all_modules() -> None:
    loaded = load_commands(MeshBot(name="ottobot"))
    assert loaded == iter_command_module_names()
    assert {"ping", "echo", "roll"} <= set(loaded)


def test_no_name_collisions_across_command_files() -> None:
    # CommandRegistry raises ValueError on duplicates; a clean load proves
    # no two files claim the same command name or alias.
    load_commands(MeshBot(name="ottobot"))


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
        load_commands(MeshBot(name="ottobot"))


def test_imported_handlers_are_not_re_registered() -> None:
    # A module that imports another command's handler must not register
    # it a second time — module_commands only picks up handlers defined
    # in the module itself.
    from ottobot.commands import ping

    impostor = types.ModuleType("impostor")
    setattr(impostor, "ping", ping.ping)
    assert module_commands(impostor) == []


def _module_with(*, name: str, **members: object) -> types.ModuleType:
    """A throwaway module whose members report it as their __module__."""
    module = types.ModuleType(name)
    for attr, value in members.items():
        if callable(value):
            value.__module__ = name  # type: ignore[attr-defined]
        setattr(module, attr, value)
    return module


def test_module_listeners_collects_marked_handlers() -> None:
    @listener
    async def watch(ctx: Context) -> None:
        return None

    module = _module_with(name="watcher", watch=watch)
    assert module_listeners(module) == [watch]


def test_register_module_registers_listeners() -> None:
    @listener
    async def watch(ctx: Context) -> None:
        return None

    bot = MeshBot(name="ottobot")
    register_module(bot, _module_with(name="watcher", watch=watch))
    assert watch in bot._listeners


def test_register_module_loads_commands_and_listeners_together() -> None:
    @command("ping")
    async def ping(ctx: Context) -> str:
        return "pong"

    @listener
    async def watch(ctx: Context) -> None:
        return None

    bot = MeshBot(name="ottobot")
    module = _module_with(name="mixed", ping=ping, watch=watch)
    commands = register_module(bot, module)
    assert [c.name for c in commands] == ["ping"]
    assert watch in bot._listeners
    assert bot.registry.get("ping") is not None


def test_listener_only_module_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ottobot.commands as commands_pkg

    @listener
    async def watch(ctx: Context) -> None:
        return None

    module = _module_with(name="ottobot.commands.watcher", watch=watch)
    monkeypatch.setattr(commands_pkg, "iter_command_module_names", lambda: ["watcher"])
    monkeypatch.setattr(commands_pkg.importlib, "import_module", lambda name: module)
    bot = MeshBot(name="ottobot")
    assert load_commands(bot) == ["watcher"]
    assert watch in bot._listeners


def test_imported_listeners_are_not_re_registered() -> None:
    @listener
    async def watch(ctx: Context) -> None:
        return None

    watch.__module__ = "real_home"
    impostor = types.ModuleType("impostor")
    setattr(impostor, "watch", watch)
    assert module_listeners(impostor) == []
