#!/usr/bin/env python3
"""Diagnostic probe for the weather-alerts conditional-fetch caching.

Fetches the Environment Canada battleboard feed the same way the bot does
(httpx, gzip), then replays the returned validators as conditional requests
to confirm they actually earn a 304. Prints one compact JSON line per run so
several runs across a day can be compared for validator drift (a load-balanced
Apache cluster can hand out different ETag/Last-Modified for identical bytes,
which silently defeats the cache).

Run: uv run --python 3.13 python scripts/probe_weather_cache.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone

import httpx

# Keep in sync with src/ottobot/tasks/weather_alerts.py
ALERTS_URL = "https://weather.gc.ca/rss/battleboard/onrm104_e.xml"


def normalize_etag(etag: str) -> str:
    if etag.endswith('-gzip"'):
        return etag[: -len('-gzip"')] + '"'
    return etag


def cond_status(headers: dict[str, str]) -> int:
    with httpx.Client(timeout=10) as client:
        return client.get(ALERTS_URL, headers=headers).status_code


def main() -> None:
    with httpx.Client(timeout=10) as client:
        r = client.get(ALERTS_URL)
    raw_etag = r.headers.get("ETag")
    last_modified = r.headers.get("Last-Modified")
    norm_etag = normalize_etag(raw_etag) if raw_etag is not None else None
    body_sha = hashlib.sha256(r.content).hexdigest()[:12]

    result = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": r.status_code,
        "content_encoding": r.headers.get("Content-Encoding"),
        "raw_etag": raw_etag,
        "norm_etag": norm_etag,
        "last_modified": last_modified,
        "body_sha12": body_sha,
        # Each conditional replay should be 304 if the cache is working.
        "inm_norm_304": (
            cond_status({"If-None-Match": norm_etag}) if norm_etag else None
        ),
        "ims_only_304": (
            cond_status({"If-Modified-Since": last_modified})
            if last_modified
            else None
        ),
        # Exactly what the bot sends (both validators, normalized ETag).
        "bot_headers_304": cond_status(
            {
                k: v
                for k, v in {
                    "If-None-Match": norm_etag,
                    "If-Modified-Since": last_modified,
                }.items()
                if v is not None
            }
        ),
    }
    print(json.dumps(result))
    # Non-zero exit if any replay failed to earn a 304 — the smoking gun.
    for key in ("inm_norm_304", "ims_only_304", "bot_headers_304"):
        val = result[key]
        if val is not None and val != 304:
            sys.exit(1)


if __name__ == "__main__":
    main()
