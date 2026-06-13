# Contributing

The easiest way to contribute is to add a command. Every command is one
file in `src/ottawa_meshbot/commands/` plus one test file, and is picked
up automatically — there is no central list to edit.

## Adding a command

1. Create `src/ottawa_meshbot/commands/<name>.py` (lowercase module name,
   matching the command). Define the handler at the top level with the
   `@command` marker — it is collected and registered when the bot loads:

   ```python
   """!greet — say hi."""

   from ottawa_meshbot import Context, command


   @command("greet", help="Say hi", aliases=("hello",))
   async def greet(ctx: Context) -> str:
       return f"Hi {ctx.sender_name or 'there'}!"
   ```

2. Create `tests/test_command_<name>.py`. Register only your module
   against a fresh `MeshBot` and dispatch test messages — see
   `tests/test_command_ping.py` for the shape.

3. Run the tests and type checker:

   ```bash
   uv sync
   uv run pytest
   uv run ty check
   ```

4. Open a pull request.

## Guidelines

- **Keep replies short.** Mesh bandwidth is precious; a reply should fit
  comfortably in a single packet.
- **Don't block.** Handlers are async and run in the bot's event loop —
  no blocking I/O or long computation.
- **Don't trust `ctx.sender_name` on channels.** It comes from the
  `"Name: message"` text convention and can be spoofed; never use it for
  authorization.
- **Names must be unique.** Duplicate command names or aliases raise at
  load time, so CI will catch collisions.
- Modules starting with `_` are skipped by discovery — use them for
  shared helpers if needed.
