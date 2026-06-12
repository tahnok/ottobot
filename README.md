# ottawa-meshbot

A small framework for building chatbots on [MeshCore](https://meshcore.co.uk/)
mesh radio networks, built on the [`meshcore`](https://pypi.org/project/meshcore/)
Python library. You define commands with a decorator; the framework handles
listening to the device, parsing `!command` messages from DMs and channels,
and routing replies back to wherever the message came from.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- A MeshCore companion device reachable over serial, BLE, or TCP

## Quick start

```bash
uv sync
uv run examples/example_bot.py --serial /dev/ttyUSB0
```

Then send the node a DM (or post on a channel it monitors) saying `!help`.

## Writing your own bot

Create a script, register commands on a `MeshBot`, and hand it to the runner:

```python
import asyncio
from ottawa_meshbot import MeshBot, Context
from ottawa_meshbot.runner import MeshCoreRunner, connect

bot = MeshBot(prefix="!")

@bot.command("ping", help="Check that the bot is alive")
async def ping(ctx: Context) -> str:
    return "pong"

@bot.command("greet", help="Say hi", aliases=("hello",))
async def greet(ctx: Context) -> str:
    return f"Hi {ctx.sender_name or 'there'}!"

async def main():
    mc = await connect(serial="/dev/ttyUSB0")
    await MeshCoreRunner(bot, mc).run_forever()

asyncio.run(main())
```

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
| `ctx.message` | The full `IncomingMessage` (text, sender key, channel index) |

A `!help` command listing every registered command is built in. Exceptions
raised by a handler are caught, logged, and reported back to the sender
instead of crashing the bot.

`MeshBot(prefix="!", respond_in_channels=True)` lets you change the command
prefix or restrict the bot to DMs only.

## Project layout

```
src/ottawa_meshbot/
  bot.py        MeshBot: command parsing and dispatch (transport-agnostic)
  commands.py   Command + CommandRegistry (names, aliases, help text)
  context.py    IncomingMessage and the Context passed to handlers
  runner.py     MeshCoreRunner: wires the bot to a meshcore device
examples/
  example_bot.py  Runnable bot with ping/echo/roll commands
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
