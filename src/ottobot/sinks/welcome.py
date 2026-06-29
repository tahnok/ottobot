"""
Greets newcomers to the mesh. It tracks
the public key of messages, and when a new client talks in public,
it sends a welcome message
"""

from ottobot import Context, sink


@sink()
async def welcome(ctx: Context) -> str | None:
    return
