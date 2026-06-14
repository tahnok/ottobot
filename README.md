# ottobot

A chatbot for Ottawa's [MeshCore](https://meshcore.io/) mesh radio
network, built on the [`meshcore`](https://pypi.org/project/meshcore/)
Python library. In a DM, just message it `!help`. On a shared channel,
mention it first — `@[ottobot] !help` — so it stays quiet unless
spoken to. Anyone can contribute a command — each one is a single file,
picked up automatically. See [Contributing a command](#contributing-a-command).

For more info see https://ottawamesh.ca/ or [join the discord](https://discord.gg/WSyNd8SfNr)

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- A MeshCore companion device reachable over serial, BLE, or TCP

## Running the bot

```bash
uv sync
uv run ottobot --serial /dev/ttyUSB0
uv run ottobot --ble AA:BB:CC:DD:EE:FF
uv run ottobot --tcp 192.168.1.50:5000
```

### Addressing the bot

In a **DM** the prefix alone is enough (`!ping`) — the message is clearly
for the bot. On a **channel** it only answers when mentioned first, so it
doesn't reply to every `!command` on a busy channel. The MeshCore app
inserts mentions as `@[Name]`; a plain or `@`-prefixed name typed by hand
works too:

```
@[ottobot] !ping
@ottobot !ping
ottobot !ping
ottobot: !ping
```

By default the bot uses the connected device's own advertised name; pass
`--name <name>` to pin a different one. A command can opt out of requiring
the name in channels with `@command(..., requires_address=False)`, for
commands meant to react to any channel message.

## Running with Docker

A prebuilt image is published to the GitHub Container Registry on every push
to `main`:

```bash
docker run --rm --device /dev/ttyUSB0 ghcr.io/tahnok/ottobot:latest --serial /dev/ttyUSB0
```

Pass the same connection flags you'd pass to `ottobot`. To talk to a
companion over the network instead of USB:

```bash
docker run --rm ghcr.io/tahnok/ottobot:latest --tcp 192.168.1.50:5000
```

A sample [`docker-compose.yml`](docker-compose.yml) is included — edit the
`command:` and device path to match your hardware, then:

```bash
docker compose up -d      # start the bot in the background
docker compose logs -f    # follow its output
```

The container runs as a non-root user, so it must be in the group that owns
the serial device on the host. Check with `stat -c '%G %g' /dev/ttyUSB0`
(typically `dialout`, GID 20) and set `group_add:` in the Compose file to
that GID.

USB devices can also enumerate under different names across reboots
(`/dev/ttyUSB0`, `/dev/ttyUSB1`, ...). A udev rule pins a stable path. Find
the adapter's attributes with `udevadm info -a -n /dev/ttyUSB0 | grep -E
'idVendor|idProduct|serial'`, then create `/etc/udev/rules.d/99-meshcore.rules`
on the host:

```
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", SYMLINK+="meshcore"
```

Reload with `sudo udevadm control --reload && sudo udevadm trigger`, then
point both `command:` and `devices:` at `/dev/meshcore`.

To build the image from a local checkout instead of pulling it:

```bash
docker build -t ottobot .
```

## Trying commands without a radio

```bash
uv run ottobot --simulate
```

opens an interactive simulator: type messages exactly as you would send
them over the mesh (`!ping`, `!roll 20`, ...) and the bot's replies are
printed back. Everything runs in memory — no device is needed and nothing
is sent over the mesh, so it's the place to test a command you're working
on before spamming a real channel.


## Commands

| Command | What it does |
|---|---|
| `!help` | List all commands |
| `!ping` | Pong back with the path your message took (and SNR when available) |
| `!echo <text>` | Repeat back whatever you send |
| `!roll [sides]` | Roll a die, default d6 (alias: `!dice`) |

## Contributing a command

Every command lives in its own file under
[`src/ottobot/commands/`](src/ottobot/commands/) and is
discovered automatically — there is no central list to edit. To add one:

1. Copy `src/ottobot/commands/ping.py` to
   `src/ottobot/commands/yourcommand.py`.
2. Define your handler at the top level with `@command(...)`. The whole
   `ping.py` looks like this:

   ```python
   """!ping — check that the bot is alive and see how your message got there."""

   from ottobot import Context, command


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

3. Try it out interactively with `uv run ottobot --simulate` — see
   [Trying commands without a radio](#trying-commands-without-a-radio).
4. Add a matching `tests/test_command_yourcommand.py` (copy
   `tests/test_command_ping.py` for the shape).
5. Run `uv run black .`, `uv run pytest`, and `uv run ty check`.
6. Open a pull request.

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Development

```bash
uv sync          # install dependencies (including dev group)
uv run black .   # auto-format the code
uv run pytest    # run the test suite
uv run ty check  # type check
```

Code is formatted with [black](https://black.readthedocs.io/); CI runs
`black --check .` alongside the tests and type check, so format before
pushing. Tests run entirely against a fake in-memory device — no radio
hardware needed.

## Notes on MeshCore behavior

- Channel messages don't carry a sender public key; the sender's name is
  recovered from the `"Name: message"` text convention, so it can be spoofed.
  Don't build channel-message authorization on top of `ctx.sender_name`.
- DM senders are identified by a 6-byte public key prefix and resolved
  against the device's contact list; DMs from unknown contacts are ignored
  (the bot has no way to reply to them).
