"""Poll Hydro Ottawa's outage map and announce significant outages.

Hydro Ottawa's outage map (outages.hydroottawa.com) is a KUBRA StormCenter
deployment serving immutable JSON snapshots from kubra.io; see
docs/hydro-ottawa-outages-api.md for the reverse-engineered layout. Every
poll first reads the tiny ``currentState`` pointer — if its ``updatedAt``
hasn't moved since the last poll the snapshot is unchanged and nothing else
is fetched. Otherwise the summary gives the headline totals, and only when
an announcement is actually due is the per-ward report fetched too.

Small outages are routine (a couple hundred customers out on a normal day),
so to avoid spamming the channel nothing is said until at least
``MIN_CUSTOMERS_AFFECTED`` customers are out (or Hydro Ottawa flips the map
into STORM mode). Once announced, the task stays quiet unless the outage
roughly doubles (``ESCALATION_FACTOR``), and sends a single all-clear when
the affected count falls below ``ALL_CLEAR_BELOW``. Announcements go out on
the "#ott-alerts" channel.

Mesh messages are limited to a single small packet, so announcements are
packed to at most ``MAX_MESSAGE_LEN`` UTF-8 bytes (bytes, not characters —
ward names like "Orléans" are not pure ASCII): as many of the worst-hit
wards as fit are listed, the rest folded into "+N more".

To be nice to KUBRA's servers, the ``currentState`` poll is conditional:
kubra.io sends no ETag but honors ``If-Modified-Since`` (verified
2026-07-16), so an unchanged pointer costs a single 304 with no body. The
snapshot files themselves are immutable and content-addressed — each one is
fetched at most once — so no validators are needed for them.

State (last seen ``updatedAt``, the ``Last-Modified`` validator, last
announced size) lives in memory only; a restart just means one full fetch,
and an ongoing significant outage is announced once on startup.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from ottobot import TaskContext, task
from ottobot.channels import OTT_ALERTS

# Stable deployment identifiers embedded in the outage map's page
# (docs/hydro-ottawa-outages-api.md); re-derive from the page if requests
# start 404ing.
BASE_URL = "https://kubra.io"
INSTANCE_ID = "75aa35eb-53c1-42e0-b705-d8abcf71334a"
VIEW_ID = "671ab4e4-6e66-453f-9179-040b44c3f155"
WARD_REPORT_ID = "2d918cbe-b083-44a1-a818-17e662d2cc35"

CURRENT_STATE_URL = (
    f"{BASE_URL}/stormcenter/api/v1/stormcenters/{INSTANCE_ID}"
    f"/views/{VIEW_ID}/currentState?preview=false"
)

# Don't announce until this many customers are without power (STORM mode
# announces regardless). ~200 customers out is an ordinary day.
MIN_CUSTOMERS_AFFECTED = 500
# Once announced, re-announce only when the outage grows by this factor...
ESCALATION_FACTOR = 2
# ...and send one all-clear when the affected count falls below this.
ALL_CLEAR_BELOW = 50

# Mesh packet budget for a single message, measured in UTF-8 bytes.
MAX_MESSAGE_LEN = 140

# currentState.updatedAt of the last fully processed snapshot.
_last_updated_at: int | None = None
# Last-Modified from the currentState response of that snapshot, sent back
# as If-Modified-Since so an unchanged pointer is a bodyless 304.
_state_last_modified: str | None = None
# Customers affected when we last announced; None = no active announcement.
_announced_customers: int | None = None


def summary_url(interval_path: str) -> str:
    return f"{BASE_URL}/{interval_path}/public/summary-1/data.json"


def ward_report_url(interval_path: str) -> str:
    return f"{BASE_URL}/{interval_path}/public/reports/{WARD_REPORT_ID}_report.json"


def _val(value: Any) -> int:
    """Unwrap KUBRA's ``{"val": n}`` wrappers; plain numbers pass through.

    Missing or null values raise rather than default to 0 — a zero conjured
    out of absent data could fire a bogus all-clear or hide a real outage.
    The runner logs a raising task and carries on, so the poll fails loudly
    and is retried instead of announcing nonsense.
    """
    if isinstance(value, dict):
        value = value.get("val")
    if value is None:
        raise ValueError("missing numeric value in KUBRA outage data")
    return int(value)


def short_ward_name(name: str) -> str:
    """Keep only the English half of bilingual ward names.

    Ward names come back as e.g. "Kanata South / Kanata-Sud"; the French
    duplicate just burns packet budget.
    """
    return name.split(" / ")[0].strip()


def affected_areas(report: Any) -> list[tuple[str, int]]:
    """(ward name, customers affected) for each ward with an outage, worst first.

    The report lists every ward, most with zero outages; only wards with
    ``n_out > 0`` are kept.
    """
    areas = [
        (short_ward_name(area.get("name") or "?"), _val(area.get("cust_a")))
        for area in report.get("file_data", {}).get("areas", [])
        if _val(area.get("n_out"))
    ]
    areas.sort(key=lambda pair: pair[1], reverse=True)
    return areas


def format_announcement(
    customers: int, outages: int, areas: list[tuple[str, int]], storm: bool
) -> str:
    """One outage announcement packed into MAX_MESSAGE_LEN UTF-8 bytes.

    Lists as many of the worst-hit wards as fit, folding the rest into
    "+N more".
    """
    mode = "STORM mode, " if storm else ""
    noun = "outage" if outages == 1 else "outages"
    head = f"Hydro Ottawa: {mode}{customers:,} customers out ({outages} {noun})"
    best = head
    for count in range(1, len(areas) + 1):
        listed = ", ".join(f"{name} {affected:,}" for name, affected in areas[:count])
        left_out = len(areas) - count
        suffix = f" +{left_out} more" if left_out else ""
        candidate = f"{head}: {listed}{suffix}"
        if len(candidate.encode("utf-8")) <= MAX_MESSAGE_LEN:
            best = candidate
    return best


def format_all_clear(customers: int) -> str:
    if customers:
        return f"Hydro Ottawa: power mostly restored, {customers:,} customers still out"
    return "Hydro Ottawa: power restored"


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


@task(
    "hydro_outages",
    interval=timedelta(minutes=10),
    channel=OTT_ALERTS,
    help="Announce significant Hydro Ottawa power outages",
)
async def hydro_outages(ctx: TaskContext) -> str | None:
    global _last_updated_at, _state_last_modified, _announced_customers
    headers = {}
    if _state_last_modified is not None:
        headers["If-Modified-Since"] = _state_last_modified
    # A failed fetch just raises: the runner (and simulator) log a raising
    # task and keep the schedule going.
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(CURRENT_STATE_URL, headers=headers)
        if response.status_code == 304:
            return None
        response.raise_for_status()
        state = response.json()
        state_last_modified = response.headers.get("Last-Modified")
        updated_at = state.get("updatedAt")
        if updated_at is not None and updated_at == _last_updated_at:
            _state_last_modified = state_last_modified
            return None
        interval_path = state["data"]["interval_generation_data"]

        summary = await _get_json(client, summary_url(interval_path))
        file_data = summary["summaryFileData"]
        totals = file_data["totals"][0]
        customers = _val(totals.get("total_cust_a"))
        outages = _val(totals.get("total_outages"))
        storm = (file_data.get("page_mode") or {}).get("mode") == "STORM"

        announcement: str | None = None
        if _announced_customers is None:
            if storm or customers >= MIN_CUSTOMERS_AFFECTED:
                report = await _get_json(client, ward_report_url(interval_path))
                announcement = format_announcement(
                    customers, outages, affected_areas(report), storm
                )
                _announced_customers = customers
        elif customers < ALL_CLEAR_BELOW and not storm:
            announcement = format_all_clear(customers)
            _announced_customers = None
        elif customers >= ESCALATION_FACTOR * max(
            _announced_customers, MIN_CUSTOMERS_AFFECTED
        ):
            report = await _get_json(client, ward_report_url(interval_path))
            announcement = format_announcement(
                customers, outages, affected_areas(report), storm
            )
            _announced_customers = customers

    # Only mark the snapshot processed (and keep its validator) once
    # everything above succeeded, so a failed downstream fetch is retried
    # unconditionally on the next poll.
    _last_updated_at = updated_at
    _state_last_modified = state_last_modified
    return announcement
