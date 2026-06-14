"""An interactive simulator for trying out commands without a radio.

    ottobot --simulate

Type messages exactly as you would send them over the mesh ("!ping",
"!roll 20", ...) and the bot's replies are printed back — nothing touches
the network. Lines starting with "/" control the simulated sender instead
of going to the bot:

    /dm                 talk to the bot in a DM
    /channel [n]        talk on channel n (default 0, where you start)
    /name <name>        change the simulated sender's name
    /hops <n> [a1,b2]   pretend messages took n repeater hops, optionally
                        via the given comma-separated hop hashes
    /status             show the simulated sender and route
    /help               list these controls
    /quit               leave the simulator
"""

from __future__ import annotations

import asyncio

from .bot import MeshBot
from .context import IncomingMessage

BANNER = (
    "Simulator: messages are handled in memory, nothing is sent over the mesh.\n"
    "On a channel, mention the bot (e.g. '@[{name}] !help'); in a DM "
    "(/dm) the prefix alone is enough.\n"
    "/help for simulator controls, /quit to leave."
)

CONTROL_HELP = [
    "/dm                 talk to the bot in a DM",
    "/channel [n]        talk on channel n (default 0, where you start)",
    "/name <name>        change the simulated sender's name",
    "/hops <n> [a1,b2]   pretend messages took n repeater hops",
    "/status             show the simulated sender and route",
    "/quit               leave the simulator",
]

# The key prefix simulated DMs carry; commands only see it via
# message.sender_key, there is no contact list to resolve it against.
FAKE_SENDER_KEY = "f00dface0042"


class Simulator:
    """Feeds typed lines into a MeshBot as if they arrived from the mesh.

    Keeps a mutable "persona" (sender name, DM vs. channel, simulated
    route) used to build each IncomingMessage. handle_line() is the
    testable core: it takes one line of user input and returns the lines
    to print. repl() wraps it in a stdin/stdout loop.
    """

    def __init__(self, bot: MeshBot) -> None:
        self.bot = bot
        self.sender_name = "you"
        self.channel_idx: int | None = 0  # start on channel 0, like the mesh default
        self.path_len: int = 255  # arrived direct, like a nearby node
        self.path: str | None = None
        self.done = False

    @property
    def prompt(self) -> str:
        where = "dm" if self.channel_idx is None else f"ch{self.channel_idx}"
        return f"{self.sender_name}@{where}> "

    def build_message(self, text: str) -> IncomingMessage:
        """The IncomingMessage the current persona's *text* arrives as."""
        return IncomingMessage(
            text=text,
            # Channel messages carry no sender key on the real mesh either.
            sender_key=FAKE_SENDER_KEY if self.channel_idx is None else None,
            sender_name=self.sender_name,
            channel_idx=self.channel_idx,
            path_len=self.path_len,
            path=self.path,
        )

    async def handle_line(self, line: str) -> list[str]:
        """Process one line of input and return the lines to print."""
        line = line.strip()
        if not line:
            return []
        if line.startswith("/"):
            return self._control(line[1:])
        replies: list[str] = []

        async def reply(text: str) -> None:
            replies.append(text)

        handled = await self.bot.dispatch(self.build_message(line), reply)
        if not handled:
            return ["(no reply — the bot ignores this, it is not a known command)"]
        out: list[str] = []
        for text in replies:
            first, *rest = text.split("\n")
            out.append(f"bot> {first}")
            out.extend(f"     {extra}" for extra in rest)
        return out

    def _control(self, body: str) -> list[str]:
        name, _, args = body.partition(" ")
        args = args.strip()
        match name.lower():
            case "dm":
                self.channel_idx = None
                return ["now talking in a DM"]
            case "channel" | "ch":
                try:
                    self.channel_idx = int(args) if args else 0
                except ValueError:
                    return [f"channel must be a number, got {args!r}"]
                return [f"now talking on channel {self.channel_idx}"]
            case "name":
                if not args:
                    return ["usage: /name <name>"]
                self.sender_name = args
                return [f"now sending as {self.sender_name}"]
            case "hops":
                return self._set_hops(args)
            case "status":
                return [self._status()]
            case "help" | "?":
                return CONTROL_HELP
            case "quit" | "exit" | "q":
                self.done = True
                return ["bye"]
            case _:
                return [f"unknown simulator control /{name} — try /help"]

    def _set_hops(self, args: str) -> list[str]:
        count_str, _, route = args.partition(" ")
        try:
            count = int(count_str)
        except ValueError:
            return ["usage: /hops <n> [a1,b2,...]"]
        if count < 0:
            return ["hop count cannot be negative"]
        if count == 0:
            self.path_len = 255  # the device's encoding for "arrived direct"
            self.path = None
        else:
            path = route.replace(",", "").strip()
            if path:
                try:
                    int(path, 16)
                except ValueError:
                    return [f"hop hashes must be hex, got {route!r}"]
            self.path_len = count
            self.path = path or None
        return [f"messages now arrive {self.build_message('').path_description}"]

    def _status(self) -> str:
        where = "a DM" if self.channel_idx is None else f"channel {self.channel_idx}"
        route = self.build_message("").path_description
        addressing = (
            "DM: prefix alone"
            if self.channel_idx is None
            else f"mention as @[{self.bot.name}]"
        )
        return (
            f"{self.sender_name} in {where}, messages arrive {route} " f"({addressing})"
        )

    async def repl(self) -> None:
        """Read lines from stdin and print the bot's side until /quit or EOF."""
        print(BANNER.format(name=self.bot.name))
        while not self.done:
            try:
                line = await asyncio.to_thread(input, self.prompt)
            except (EOFError, KeyboardInterrupt):
                print()
                break
            for out in await self.handle_line(line):
                print(out)
