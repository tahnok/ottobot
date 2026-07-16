#!/usr/bin/env python3
"""Scan Ontario battleboard feeds for any region with >1 active alert.

Used to confirm in the wild whether Environment Canada issues co-active
alerts (e.g. a Heat Warning + a Special Air Quality Statement) that share
the same trailing id timestamp while differing only in the `_wN` slot — the
collision that makes weather_alerts.alert_key() collapse them into one dedup
key and drop all but one. Prints every entry's (slot-stripped key, title)
for any multi-alert region so timestamp collisions are visible.

Run: uv run --python 3.13 python scripts/scan_multi_alerts.py
"""

from __future__ import annotations

import re
from xml.etree import ElementTree

import httpx

_ATOM = {"atom": "http://www.w3.org/2005/Atom"}
_SLOT_RE = re.compile(r"_w\d+(?=:)")

# A spread of Ontario battleboard regions (numbered-region + rm codes).
REGIONS = [f"onrm{n}" for n in range(85, 180)] + [f"on{n}" for n in range(1, 60)]


def scan_one(client: httpx.Client, region: str) -> None:
    url = f"https://weather.gc.ca/rss/battleboard/{region}_e.xml"
    try:
        r = client.get(url, headers={"Accept-Encoding": "gzip"})
        if r.status_code != 200 or "<feed" not in r.text[:200]:
            return
        root = ElementTree.fromstring(r.text)
    except Exception:
        return
    entries = root.findall("atom:entry", _ATOM)
    rows = []
    for e in entries:
        title = (e.findtext("atom:title", "", _ATOM) or "").strip()
        aid = (e.findtext("atom:id", "", _ATOM) or "").strip()
        if aid and "No alerts in effect" not in title:
            rows.append((aid, _SLOT_RE.sub("", aid), title))
    if len(rows) >= 2:
        print(f"\n### {region}: {len(rows)} active alerts")
        keys = [k for _, k, _ in rows]
        for aid, key, title in rows:
            print(f"  id={aid}\n    key={key}\n    title={title}")
        if len(set(keys)) < len(keys):
            print("  >>> COLLISION: two alerts share a dedup key (bug would drop one)")


def main() -> None:
    found = False
    with httpx.Client(timeout=10) as client:
        for region in REGIONS:
            before = None  # marker unused; scan_one prints directly
            scan_one(client, region)
    if not found:
        print("(scan complete)")


if __name__ == "__main__":
    main()
