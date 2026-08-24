"""
!flyby — check overhead aircraft via ADSB.lol.

Version: 0.1

Changelog:
- v0.1: Initial release. Added ADSB.lol integration, 140-character mesh
        truncation safeguard, filtered out grounded aircraft, compass headings,
        operator identification, rich aircraft descriptions, and
        comprehensive Ottawa region dictionary.
"""

import aiohttp
from ottobot import Context, command

# Default to downtown Ottawa
DEFAULT_LAT = 45.4236
DEFAULT_LON = -75.7009
DEFAULT_RADIUS_NM = 10  # Nautical miles

# Common operators in the Ottawa area (by ICAO callsign prefix)
OPERATORS = {
    "ACA": "Air Canada",
    "JZA": "Jazz",
    "ROU": "Rouge",
    "WJA": "WestJet",
    "WEN": "WestJet Encore",
    "POE": "Porter",
    "FLA": "Flair",
    "SWG": "Sunwing",
    "TSC": "Air Transat",
    "MPE": "Canadian North",
    "CFC": "RCAF",
    "UAL": "United",
    "DAL": "Delta",
    "AAL": "American",
}

# Pre-defined lookup for specific areas
REGIONS = {
    "barrhaven": (45.2736, -75.7412),
    "carp": (45.3492, -76.0381),
    "constancebay": (45.4950, -76.0769),
    "cumberland": (45.5186, -75.4055),
    "gatineau": (45.4765, -75.7013),
    "glebe": (45.4021, -75.6888),
    "gloucester": (45.4332, -75.5905),
    "greely": (45.2578, -75.5812),
    "kanata": (45.3089, -75.8986),
    "manotick": (45.2269, -75.6817),
    "nepean": (45.3283, -75.7534),
    "northgower": (45.1341, -75.7145),
    "orleans": (45.4667, -75.4833),
    "osgoode": (45.1437, -75.6033),
    "richmond": (45.1950, -75.8286),
    "riversidesouth": (45.2808, -75.6819),
    "rockcliffe": (45.4526, -75.6666),
    "smithsfalls": (44.8970, -76.0197),
    "stittsville": (45.2729, -75.9146),
    "vanier": (45.4371, -75.6580),
    "westboro": (45.3941, -75.7516),
    "yow": (45.3225, -75.6692),  # Ottawa Airport
}


@command("flyby", help="List aircraft flying overhead. Usage: !flyby [region]")
async def flyby(ctx: Context) -> str:
    # Parse the user's requested region, if any
    requested = ctx.args.strip().lower().replace(" ", "")

    if requested and requested in REGIONS:
        lat, lon = REGIONS[requested]
        location_name = requested.title()
    elif requested:
        # If they typed a region we don't have, let them know what's available
        available = ", ".join(REGIONS.keys())
        return f"Unknown region. Try: {available} (or leave blank for downtown)"
    else:
        lat, lon = DEFAULT_LAT, DEFAULT_LON
        location_name = "Central Ottawa"

    # ADSB.lol point API (free, no key required)
    url = f"https://api.adsb.lol/v2/point/{lat}/{lon}/{DEFAULT_RADIUS_NM}"

    async with aiohttp.ClientSession() as session:
        try:
            # Fixed the timeout syntax for the strict static type checker
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status != 200:
                    return "Error: Could not reach flight data API."
                data = await response.json()
        except Exception:
            return "Error: Network timeout reaching flight API."

    aircraft_list = data.get("ac", [])

    if not aircraft_list:
        return f"No aircraft detected within {DEFAULT_RADIUS_NM}NM of {location_name}."

    # Helper to convert altitude to integer and catch "ground" strings
    def get_altitude(ac: dict) -> int:
        alt = ac.get("alt_baro")
        if alt == "ground" or alt is None:
            return 0
        try:
            return int(alt)
        except (ValueError, TypeError):
            return 0

    # Filter out missing, invalid, or grounded aircraft (altitude <= 0)
    visible = [ac for ac in aircraft_list if get_altitude(ac) > 0]

    if not visible:
        return f"No airborne aircraft detected within {DEFAULT_RADIUS_NM}NM of {location_name}."

    # Sort by altitude, lowest first (most likely to be seen/heard)
    visible.sort(key=get_altitude)

    # Helper to convert a 360-degree heading into a compass direction
    def get_compass_direction(degrees: float | int | None) -> str:
        if degrees is None:
            return ""
        try:
            dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
            ix = round(float(degrees) / 45.0) % 8
            return dirs[ix]
        except (ValueError, TypeError):
            return ""

    # Format the top 2 airborne aircraft to keep the expanded text compact
    results = []
    for ac in visible[:2]:
        flight = str(ac.get("flight") or ac.get("r") or "").strip()

        # 1. Identify Operator or fallback to Registration/Callsign
        operator = flight
        if len(flight) >= 3 and flight[:3].upper() in OPERATORS:
            operator = OPERATORS[flight[:3].upper()]
        elif not operator:
            operator = "Unknown"

        # 2. Identify Aircraft Type
        desc_raw = ac.get("desc", "")
        if desc_raw:
            # Title case it, but cap it at 20 characters so it doesn't get too long
            ac_name = desc_raw.title()
            if len(ac_name) > 22:
                ac_name = ac_name[:20] + ".."
        else:
            # Fallback to the short type code (e.g. C185) if description is missing
            ac_name = ac.get("t", "?")

        alt = get_altitude(ac)
        track = ac.get("track")

        alt_text = f"at {alt}ft"
        direction = get_compass_direction(track)
        dir_text = f" heading {direction}" if direction else ""

        # E.g. "Air Canada Boeing 737-800 at 34000ft heading NE"
        results.append(f"{operator} {ac_name} {alt_text}{dir_text}")

    msg = f"Over {location_name}: " + ", ".join(results)

    # Hard safe-guard for the ~140 character mesh limit
    if len(msg) > 140:
        msg = msg[:137] + "..."

    return msg
