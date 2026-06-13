# ottawa-meshbot

A chatbot for Ottawa's [MeshCore](https://meshcore.co.uk/) mesh radio
network, built on the [`meshcore`](https://pypi.org/project/meshcore/)
Python library. Message it `!help` on the mesh (in a DM or on a channel it
monitors) and it answers. Anyone can contribute a command — each one is a
single file, picked up automatically. See
[Contributing a command](#contributing-a-command).

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- A MeshCore companion device reachable over serial, BLE, or TCP

## Running the bot

```bash
uv sync
uv run ottawa-meshbot --serial /dev/ttyUSB0
uv run ottawa-meshbot --ble AA:BB:CC:DD:EE:FF
uv run ottawa-meshbot --tcp 192.168.1.50:5000
```

(`uv run python -m ottawa_meshbot ...` works too.)

## Commands

| Command | What it does |
|---|---|
| `!help` | List all commands |
| `!ping` | Pong back with the path your message took (and SNR when available) |
| `!echo <text>` | Repeat back whatever you send |
| `!roll [sides]` | Roll a die, default d6 (alias: `!dice`) |

## Contributing a command

Every command lives in its own file under
[`src/ottawa_meshbot/commands/`](src/ottawa_meshbot/commands/) and is
discovered automatically — there is no central list to edit. To add one:

1. Copy `src/ottawa_meshbot/commands/ping.py` to
   `src/ottawa_meshbot/commands/yourcommand.py`.
2. Define your handler at the top level with `@command(...)`. The whole
   `ping.py` looks like this:

   ```python
   """!ping — check that the bot is alive and see how your message got there."""

   from ottawa_meshbot import Context, command


   @command("ping", help="Check that the bot is alive")
   async def ping(ctx: Context) -> str:
       pong = f"pong ({ctx.path_description})"
       # ctx.raw is the full meshcore payload, for fields the framework
       # doesn't model — e.g. SNR, reported by firmware protocol v3+.
       snr = (ctx.raw or {}).get("SNR")
       if snr is not None:
           pong += f" SNR {snr}dB"
       return pong
   ```

3. Add a matching `tests/test_command_yourcommand.py` (copy
   `tests/test_command_ping.py` for the shape).
4. Run `uv run pytest` and `uv run ty check`.
5. Open a pull request.

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Writing a handler

Command handlers are async functions that receive a `Context` and can:

- **return a string** — it is sent as the reply, or
- **call `await ctx.reply("...")`** — useful for sending multiple replies, or
- **return `None`** — nothing is sent.

Useful things on `Context`:

| Attribute | Meaning |
|---|---|
| `ctx.args` | Everything after the command name, e.g. `"20"` for `!roll 20` |
| `ctx.sender_name` | Sender's name (contact name for DMs, `Name:` text convention for channels) |
| `ctx.is_dm` | `True` for direct messages, `False` for channel messages |
| `ctx.path_description` | Route the message took, e.g. `"direct"` or `"2 hops via a1,b2"` |
| `ctx.raw` | The unmodified meshcore event payload (SNR, sender_timestamp, ...) — escape hatch for fields the framework doesn't model |
| `ctx.message` | The full `IncomingMessage` (text, sender key, channel index, path) |

A `!help` command listing every registered command is built in. Exceptions
raised by a handler are caught, logged, and reported back to the sender
instead of crashing the bot.

## Project layout

```
src/ottawa_meshbot/
  bot.py        MeshBot: command parsing and dispatch (transport-agnostic)
  registry.py   Command + CommandRegistry (names, aliases, help text)
  context.py    IncomingMessage and the Context passed to handlers
  runner.py     MeshCoreRunner: wires the bot to a meshcore device
  cli.py        The ottawa-meshbot entry point
  commands/     The bot's commands, one file each — add yours here
tests/
```

## Development

```bash
uv sync          # install dependencies (including dev group)
uv run pytest    # run the test suite
uv run ty check  # type check
```

Tests run entirely against a fake in-memory device — no radio hardware needed.

## Notes on MeshCore behavior

- Channel messages don't carry a sender public key; the sender's name is
  recovered from the `"Name: message"` text convention, so it can be spoofed.
  Don't build channel-message authorization on top of `ctx.sender_name`.
- DM senders are identified by a 6-byte public key prefix and resolved
  against the device's contact list; DMs from unknown contacts are ignored
  (the bot has no way to reply to them).
