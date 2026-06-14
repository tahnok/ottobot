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
uv sync          # install deps (including dev group)
uv run pytest    # run the test suite
uv run ty check  # type check (ty, not mypy)
```

Always run both `uv run pytest` and `uv run ty check` before considering a
change done — CI runs exactly these two steps (`.github/workflows/ci.yml`).

## Architecture

The codebase is layered so the core bot logic is transport-agnostic:

- **`src/ottobot/bot.py` — `MeshBot`**: Core dispatch. Parses `!command args`,
  looks up the command in the registry, builds a `Context`, calls the
  handler, and sends the return value (if any) as the reply. Exceptions
  from handlers are caught, logged, and replaced with a generic error
  reply — handlers don't need their own try/except for unexpected errors.
  Also registers the built-in `!help` command. Knows nothing about
  meshcore/radios.

- **`src/ottobot/context.py` — `IncomingMessage` and `Context`**: Normalized
  representation of an incoming message, independent of transport.
  `IncomingMessage` carries text, sender info, channel/DM info, and path
  (hop) info, plus `raw` as an escape hatch for transport-specific fields
  (e.g. SNR). `Context` is what handlers receive: read-only view of the
  message plus `ctx.reply(text)` to send additional replies.

- **`src/ottobot/registry.py` — `Command`, `CommandRegistry`, `command`
  decorator**: `@command(name, help=..., aliases=...)` tags a top-level
  async function with command metadata at import time (no bot instance
  needed). `module_commands()` collects tagged handlers defined directly in
  a module (not merely imported). `CommandRegistry` resolves names/aliases
  to commands and raises on duplicate registration.

- **`src/ottobot/commands/`**: One file per command, auto-discovered by
  `load_commands()` (`commands/__init__.py`). Modules starting with `_` are
  skipped (use for shared helpers). A command module with zero
  `@command`-marked handlers raises `TypeError` at load time — load is
  fail-fast by design (broken commands should stop the bot, not be
  silently skipped).

- **`src/ottobot/runner.py` — `MeshCoreRunner`, `connect()`**: The only
  module that talks to the real `meshcore` library. Subscribes to
  `CONTACT_MSG_RECV`/`CHANNEL_MSG_RECV` events, normalizes payloads into
  `IncomingMessage`, and wires replies back to `send_msg`/`send_chan_msg`.

- **`src/ottobot/simulator.py` — `Simulator`**: In-memory REPL for trying
  commands without a device. Maintains a "persona" (sender name, DM vs.
  channel, simulated hop count/path) controllable via `/dm`, `/channel`,
  `/name`, `/hops`, `/status` etc. `handle_line()` is the testable core.

- **`src/ottobot/cli.py`**: Argument parsing and entry point (`ottobot`
  script). Builds the bot via `build_bot()` (loads all commands), then
  either runs the simulator (`--simulate`) or connects to a device
  (`--serial` / `--ble` / `--tcp`) and runs `MeshCoreRunner.run_forever()`.

## Key conventions

- **Adding a command**: create `src/ottobot/commands/<name>.py` with a
  top-level `@command("name", help="...", aliases=(...))` async handler
  taking `ctx: Context` and returning `str | None`. Add a matching
  `tests/test_command_<name>.py` (copy `test_command_ping.py` for shape —
  it registers just that module against a fresh `MeshBot()` and dispatches
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
