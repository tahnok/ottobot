# CLAUDE.md

Guidance for AI assistants (Claude Code) working in this repository.

## What this is

Ottobot is a chatbot for Ottawa's MeshCore mesh radio network. It connects
to a MeshCore companion device (serial/BLE/TCP), listens for DMs and
channel messages, and responds to `!`-prefixed commands. Commands are
community-contributed, one file each, auto-discovered — there is no
central registry to edit.

## Requirements & setup

- Python 3.13+ (see `.python-version`)
- Dependency/task runner: [`uv`](https://docs.astral.sh/uv/)

```bash
uv sync               # install deps (including dev group)
uv run black .        # auto-format the code (black)
uv run pytest         # run the test suite
uv run ty check       # type check (ty, not mypy)
```

Always run `uv run black --check .`, `uv run pytest`, and `uv run ty check`
before considering a change done — CI runs exactly these three steps
(`.github/workflows/ci.yml`). Run `uv run black .` to fix any formatting the
check flags.

## Architecture

The core bot logic (`bot.py`, `context.py`, `registry.py`) is
transport-agnostic and knows nothing about meshcore/radios. `runner.py` is
the only module that talks to the real `meshcore` library; `simulator.py`
provides an in-memory REPL for trying commands without a device. Commands
live one-per-file in `src/ottobot/commands/`, auto-discovered by
`load_commands()`. `cli.py` wires it all together as the `ottobot` entry
point.

## Key conventions

- **Adding a command**: create `src/ottobot/commands/<name>.py` with a
  top-level `@command("name", help="...", aliases=(...))` async handler
  taking `ctx: Context` and returning `str | None`. Add a matching
  `tests/test_command_<name>.py` (copy `test_command_ping.py` for shape —
  it registers just that module against a fresh `MeshBot(name=...)` and dispatches
  test messages via `tests/helpers.py`'s `dm()`/`channel_msg()`/
  `ReplyRecorder`). Try it interactively with `uv run ottobot --simulate`.
- **Module docstrings double as help text/usage** — e.g.
  `"""!ping — check that the bot is alive..."""`. Follow this style for new
  commands.
- **Don't trust `ctx.sender_name` on channel messages** — it's recovered
  from a spoofable `"Name: message"` text convention, never use it for
  authorization. DMs are identified by a real (resolved) sender key.
- **Handlers must not block** — they run in the bot's single event loop. No
  blocking I/O or long computation; use `asyncio`-friendly APIs.
- **Keep replies short** — mesh bandwidth is limited; aim for a single
  packet.
- **Command/alias names must be unique** — duplicates raise `ValueError` at
  registration time (caught by CI via `load_commands()` at bot construction
  / tests).
- Returning `None` from a handler sends no reply; returning a `str` sends
  it; call `await ctx.reply(...)` directly for multiple replies.

## Testing notes

Tests run against a fake in-memory bot/device — no hardware needed.
`tests/conftest.py` provides `bot`/`reply` fixtures; `tests/helpers.py`
provides `dm()`, `channel_msg()`, and `ReplyRecorder`. `pytest-asyncio` is
in `auto` mode, so async tests need no extra decorators.
