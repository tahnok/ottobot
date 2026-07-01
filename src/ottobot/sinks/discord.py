"""Mirror public-channel messages to Discord via an incoming webhook.

Set the webhook URL in the config to enable it::

    [discord]
    webhook_url = "https://discord.com/api/webhooks/.../..."

Every message seen on the public channel (the channel named "public" in
the config, or index 0 if none is named that) is POSTed to the webhook as
``{"username": <sender>, "content": "[<channel>] <text>"}`` — the channel
name is prefixed to the text so people off the radio can see where it came
from. DMs and other channels are left alone. With no webhook_url configured
the sink is inert.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ottobot import Context, sink

logger = logging.getLogger(__name__)


def _channel_name(ctx: Context, idx: int) -> str:
    """The configured name for channel *idx*, defaulting to "public"."""
    for channel in ctx.config.channels:
        if channel.index == idx:
            return channel.name
    return "public"


async def post_to_discord(url: str, payload: dict[str, Any]) -> None:
    """POST *payload* as JSON to the Discord webhook at *url*."""
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, json=payload)


@sink()
async def discord(ctx: Context) -> None:
    url = ctx.config.discord_webhook_url
    idx = ctx.message.channel_idx
    if not url or idx is None:
        return
    if idx != ctx.config.public_channel_idx():
        return
    text = ctx.message.text.strip()
    if not text:
        return
    channel_name = _channel_name(ctx, idx)
    payload = {
        "username": ctx.message.sender_name or "mesh",
        "content": f"[{channel_name}] {text}",
    }
    try:
        await post_to_discord(url, payload)
    except Exception:
        logger.warning("failed to post message to Discord webhook", exc_info=True)
