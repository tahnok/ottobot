"""Mirror public-channel messages to Discord via an incoming webhook.

Set the webhook URL in the config to enable it::

    [discord]
    webhook_url = "https://discord.com/api/webhooks/.../..."

Every message seen on the public channel is POSTed to the webhook as
``{"username": <sender>, "content": "[public] <text>"}`` — the channel name
is prefixed to the text so people off the radio can see where it came from.
DMs and other channels are left alone. With no webhook_url configured the
sink is inert.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ottobot import Context, sink
from ottobot.channels import PUBLIC

logger = logging.getLogger(__name__)


async def post_to_discord(url: str, payload: dict[str, Any]) -> None:
    """POST *payload* as JSON to the Discord webhook at *url*."""
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, json=payload)


@sink()
async def discord(ctx: Context) -> None:
    url = ctx.config.discord_webhook_url
    if not url or ctx.message.channel_idx != PUBLIC.index:
        return
    text = ctx.message.text.strip()
    if not text:
        return
    payload = {
        "username": ctx.message.sender_name or "mesh",
        "content": f"[{PUBLIC.name}] {text}",
    }
    try:
        await post_to_discord(url, payload)
    except Exception:
        logger.warning("failed to post message to Discord webhook", exc_info=True)
