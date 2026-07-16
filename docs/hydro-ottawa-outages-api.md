# Hydro Ottawa outage API (reverse‑engineered)

How to poll <https://outages.hydroottawa.com/> for current power outages.

There is **no documented public API**. The outage map is a hosted
[KUBRA StormCenter](https://kubra.io/) deployment, and the map's data is
served as plain static JSON from `kubra.io`. No API key, login, or special
headers are required — anonymous `GET` requests work. The notes below were
derived by inspecting what the map's front‑end fetches (2026‑06‑30).

> A `https://api.hydroottawa.com/v2/outages/...` endpoint is referenced by
> some third‑party sites, but it currently returns HTTP 500 and is **not**
> what the live map uses. Use the KUBRA endpoints documented here.

## Deployment identifiers

These are embedded in the page (`var BOOTSTRAP_CONFIG`) and are stable:

| Field        | Value                                  |
|--------------|----------------------------------------|
| host         | `https://kubra.io`                     |
| `instanceId` | `75aa35eb-53c1-42e0-b705-d8abcf71334a` |
| `viewId`     | `671ab4e4-6e66-453f-9179-040b44c3f155` |

## How the data is laid out

The data is published in **immutable, content‑addressed snapshots**. Every
refresh (~every 15 minutes) writes a brand‑new directory whose name is a
random GUID, then flips a single pointer (`currentState`) to it. **The data
paths therefore change on every refresh** — you cannot hard‑code them. A
poller must always do two steps:

1. `GET currentState` → read the current snapshot path
   (`data.interval_generation_data`, e.g. `data/<guid>`).
2. `GET` the summary / reports / cluster files **under that path**.

### 1. currentState (the pointer — call this first, every poll)

```
GET https://kubra.io/stormcenter/api/v1/stormcenters/{instanceId}/views/{viewId}/currentState?preview=false
```

Returns (trimmed):

```json
{
  "stormcenterDeploymentId": "fa5c4be8-143c-42fb-8ed2-de34b28e0dea",
  "updatedAt": 1782855267000,
  "data": {
    "interval_generation_data": "data/b55f1b42-9699-4946-a454-6222eee5e4f1",
    "cluster_interval_generation_data": "cluster-data/{qkh}/972f8d91-d305-4ba7-a702-c57d34722ca0/b55f1b42-9699-4946-a454-6222eee5e4f1"
  }
}
```

`interval_generation_data` (`data/<guid>`) is the base path for the summary
and report files below. `{qkh}` in the cluster path is a map‑tile quadkey
placeholder (see §4).

### 2. Summary — headline totals (best for "is anything out?")

```
GET https://kubra.io/{interval_generation_data}/public/summary-1/data.json
```

```json
{
  "summaryFileData": {
    "totals": [{
      "total_outages": 4,
      "total_cust_a": { "val": 202 },          // customers affected
      "total_cust_s": 378693,                  // customers served (total)
      "total_percent_cust_a": { "val": 0.06 },
      "total_percent_cust_active": { "val": 99.94 }
    }],
    "date_generated": "2026-06-30T21:34:27Z",
    "page_mode": { "mode": "BLUESKY" }          // BLUESKY = normal; STORM = storm mode
  }
}
```

### 3. Reports — per‑area breakdown (best for "where, and ETR?")

Two reports break the outages down by City of Ottawa **ward** and by
**neighbourhood**. The report IDs are stable and come from the deployment
config (§5):

```
# ward report
GET https://kubra.io/{interval_generation_data}/public/reports/2d918cbe-b083-44a1-a818-17e662d2cc35_report.json
# neighbourhood report
GET https://kubra.io/{interval_generation_data}/public/reports/ae0c6729-d798-4651-a972-a5c373618bc3_report.json
```

Each lists **every** area (most with zero outages); filter to
`n_out > 0`:

```json
{
  "file_data": { "areas": [{
    "name": "Somerset",
    "n_out": 1,                      // outages in this area
    "cust_a": { "val": 162 },        // customers affected
    "cust_s": 24803,                 // customers served in area
    "percent_cust_a": { "val": 0.65 },
    "etr": "2026-06-30T23:00:00Z",   // ISO time, or "ETR-EXP" (estimate expired) or null
    "etr_confidence": null,
    "gotoMap": { "bbox": [-75.729, 45.397, -75.680, 45.427] }   // [W,S,E,N]
  }]}
}
```

This is the most useful endpoint for a text bot: it gives a human‑readable
location, customers affected, and an ETR per outage, with no map‑tile math.

### 4. Cluster layer — point locations (optional)

The map pins come from a clustered vector tile layer addressed by Bing‑style
**quadkeys**. Combine the cluster path from `currentState` (replacing
`{qkh}` with the quadkey) with the per‑tile filename `public/cluster-1/{q}.json`:

```
GET https://kubra.io/cluster-data/{quadkey}/972f8d91-d305-4ba7-a702-c57d34722ca0/{interval_guid}/public/cluster-1/{quadkey}.json
```

Notes / gotchas:

- Responses are **gzip‑encoded JSON** (most clients decompress
  transparently; raw `urllib` needs a manual `gzip.decompress`).
- Tiles are **sparse**: a tile only exists where there is data at that zoom,
  and a child tile can exist while its parent 404s — so you cannot walk the
  tree top‑down. Compute the quadkeys for the territory at a fixed zoom and
  request those.
- When several outages are close together they are merged into a single
  `"cluster": true` feature with an aggregate `n_out` / `cust_a` and a single
  ETR — individual per‑incident fields (`cause`, `inc_id`, `crew_status`)
  come back `null` while clustered.

```json
{ "file_data": [{
  "id": "3-0", "title": "Area Outage",
  "desc": { "n_out": 4, "cust_a": { "val": 202 }, "cluster": true,
            "etr": "2026-06-30T23:00:00Z", "start_time": "2026-06-30T13:05:48Z",
            "cause": null, "crew_status": null, "inc_id": null },
  "geom": { "p": ["qvusGzrnmM"] }     // Google-encoded polyline (precision 5) = centroid
}]}
```

**For polling, prefer the reports (§3) over the cluster tiles** — they carry
the same outage/customer/ETR information keyed by a readable area name and
need no quadkey or polyline decoding.

### 5. Deployment config (reference only — don't poll this)

The full layer/report layout (including the report IDs above) lives in the
config deployment; handy if Hydro Ottawa ever changes the report GUIDs:

```
GET https://kubra.io/stormcenter/api/v1/stormcenters/{instanceId}/views/{viewId}/configuration/{stormcenterDeploymentId}?preview=false
```

## Recommended polling recipe

- **Cadence:** the map regenerates roughly every 15 minutes; polling every
  5–15 minutes is plenty. Don't poll faster — the data won't change and it's
  a third‑party CDN. Cache on `currentState.updatedAt`: if it hasn't moved,
  the snapshot is unchanged, so skip the downstream fetches.
- **Order:** `currentState` → `summary` (cheap gate) → if
  `total_outages > 0`, fetch the ward (and/or neighbourhood) report.
- **Watch `page_mode.mode`:** `STORM` means Hydro Ottawa has switched to
  storm mode (widespread outages); worth surfacing.
- **ETR values** are an ISO‑8601 string, the literal `"ETR-EXP"` (estimate
  has expired / not yet available), or `null`.

## Working example (stdlib only)

```python
import gzip, json, urllib.request

INSTANCE = "75aa35eb-53c1-42e0-b705-d8abcf71334a"
VIEW = "671ab4e4-6e66-453f-9179-040b44c3f155"
WARD_REPORT = "2d918cbe-b083-44a1-a818-17e662d2cc35"
BASE = "https://kubra.io"

def _get(url):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass  # not gzipped
    return json.loads(raw)

def poll_outages():
    state = _get(f"{BASE}/stormcenter/api/v1/stormcenters/{INSTANCE}"
                 f"/views/{VIEW}/currentState?preview=false")
    interval = state["data"]["interval_generation_data"]      # rotates each refresh!

    summary = _get(f"{BASE}/{interval}/public/summary-1/data.json")
    t = summary["summaryFileData"]["totals"][0]
    result = {
        "generated": summary["summaryFileData"]["date_generated"],
        "mode": summary["summaryFileData"]["page_mode"]["mode"],
        "outages": t["total_outages"],
        "customers_affected": t["total_cust_a"]["val"],
        "areas": [],
    }
    if result["outages"]:
        ward = _get(f"{BASE}/{interval}/public/reports/{WARD_REPORT}_report.json")
        for a in ward["file_data"]["areas"]:
            if a.get("n_out"):
                result["areas"].append({
                    "ward": a["name"],
                    "n_out": a["n_out"],
                    "cust_a": a["cust_a"]["val"],
                    "etr": a["etr"],
                })
    return result

if __name__ == "__main__":
    print(json.dumps(poll_outages(), indent=2))
```

Example output:

```json
{
  "generated": "2026-06-30T21:34:27Z",
  "mode": "BLUESKY",
  "outages": 4,
  "customers_affected": 202,
  "areas": [
    { "ward": "Kanata South / Kanata-Sud", "n_out": 1, "cust_a": 14,  "etr": "ETR-EXP" },
    { "ward": "Gloucester-Southgate",      "n_out": 1, "cust_a": 25,  "etr": "2026-06-30T23:00:00Z" },
    { "ward": "Stittsville",               "n_out": 1, "cust_a": 9,   "etr": "2026-06-30T22:00:00Z" },
    { "ward": "Somerset",                  "n_out": 1, "cust_a": 162, "etr": "ETR-EXP" }
  ]
}
```

## Caveats

- **Unofficial.** These endpoints are an implementation detail of a
  third‑party vendor (KUBRA) and can change without notice. The deployment
  IDs, report GUIDs, and product version (`5.51.0` at time of writing) may
  all move. Re‑derive them from the page / config (§5) if requests start
  404ing.
- All handlers in this bot run on a single event loop — if you wire this
  into an ottobot command, use an async HTTP client (e.g. `aiohttp`) and a
  short cache, not the blocking `urllib` example above. See `CLAUDE.md`
  ("Handlers must not block").
