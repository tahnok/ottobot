# ottobot

A chatbot for Ottawa's [MeshCore](https://meshcore.io/) mesh radio
network, built on the [`meshcore`](https://pypi.org/project/meshcore/)
Python library. On a shared channel, mention it first —
`@[ottobot] !help` — so it stays quiet unless spoken to. Anyone can
contribute a command — each one is a single file, picked up automatically.
See [Contributing a command](#contributing-a-command).

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

The bot only answers when mentioned first, so it doesn't reply to every
`!command` on a busy channel. The MeshCore app inserts mentions as
`@[Name]`; a plain or `@`-prefixed name typed by hand works too:

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

## Config file

A TOML config file can act as the source of truth for the device's
**name**, **channels**, **key pair**, and **radio params**. On startup the
bot connects, then pushes whatever the file specifies onto the radio, so the
device always matches the file — handy for reproducing a node after a
re-flash or device swap. Connection flags (`--serial`/`--ble`/`--tcp`) stay
on the command line.

```bash
uv run ottobot --serial /dev/ttyUSB0 --config ottobot.toml
```

Copy [`ottobot.example.toml`](ottobot.example.toml) to `ottobot.toml` and
edit it. Every field is optional; anything you omit is left untouched on the
device. The name precedence is `--name` > config `name` > the device's own
advertised name. Because the file can hold the bot's private key, the real
`ottobot.toml` is gitignored — keep it out of version control.

The config also has an optional `database` key for the sqlite file stateful
sinks use (the welcome sink greets each channel name once and remembers who
it has seen there). It defaults to `ottobot.db` in the working directory; in
Docker, set it to `/data/ottobot.db` so it persists on the bind-mounted
`./data` dir.

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

To use a [config file](#config-file), bind-mount it into the container and
point `--config` at the mount path (the container's working directory is
`/app`):

```bash
docker run --rm --device /dev/ttyUSB0 \
  -v "$PWD/ottobot.toml:/app/ottobot.toml:ro" \
  ghcr.io/tahnok/ottobot:latest --serial /dev/ttyUSB0 --config /app/ottobot.toml
```

A sample [`docker-compose.yml`](docker-compose.yml) is included — edit the
`command:` and device path to match your hardware, then:

```bash
docker compose up -d      # start the bot in the background
docker compose logs -f    # follow its output
```

The Compose file already wires up the config mount; copy
`ottobot.example.toml` to `ottobot.toml` next to it first (or drop the
`--config` flag and `volumes:` block to run without one).

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

### Deploying with Docker

The `deploy` directory contains supplemental deployment files, including
simple backup and update cron scripts, plus a lightweight log server that
can be used to inspect the Docker container logs remotely.

#### Log Server

The log server is a simple Python server intended to run as a `systemd`
service behind a reverse proxy such as nginx or Caddy with basic authentication.

A sample `systemd` service file is included to run the log server. Copy it
to `/etc/systemd/system/`, adjust the paths and user/group as needed, then
daemon-reload, enable and start the service.

Authentication is not built into the log server itself. By default, it accepts
remote connections, but it only responds to requests from the reverse proxy IP
configured by `ALLOWED_PROXY_IP`. It also expects standard `X-Forwarded-*`
headers, which are normally set by the reverse proxy.

To disable the reverse proxy header check, set:

```python
REQUIRE_REVERSE_PROXY_HEADERS = False
```

To restrict the server to local connections only, set:

```python
HOST = '127.0.0.1'
```

When `HOST` is set to `127.0.0.1`, proxy IP and forwarded header checks
are ignored.

## Trying commands without a radio

```bash
uv run ottobot --simulate
```

opens an interactive simulator: type messages exactly as you would send
them over the mesh (`@[ottobot] !ping`, `@[ottobot] !roll 20`, ...) and the
bot's replies are printed back. Everything runs in memory — no device is
needed and nothing
is sent over the mesh, so it's the place to test a command you're working
on before spamming a real channel.


## Commands

| Command | What it does |
|---|---|
| `!help` | List all commands |
| `!ping` | Pong back, addressed to you, with the path your message took |
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
       who = ctx.sender_name or "you"
       return f"@[{who}] pong ({ctx.path_description})"
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
  Don't build authorization on top of `ctx.sender_name`.
- The bot listens on channels only — it doesn't act on direct messages.
  Address it by mentioning its name (`@[ottobot] !ping`).
