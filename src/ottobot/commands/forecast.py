# ==============================================================================
# VERSION: 0.4.4
# LINES CHANGED: ~5 lines modified
# CHANGELOG:
# - Incremented patch version to 0.4.4.
# - Updated `get_coords` return signature to `tuple[str | None, str | None, str]`
#   to resolve type-checker errors when returning `None` on failed geocoding.
# ==============================================================================

"""!forecast — check the short-term weather forecast for Ottawa and other cities."""

import urllib.request
import urllib.parse
import asyncio
import re
import time
import json
from ottobot import Context, command


def get_coords(city: str) -> tuple[str | None, str | None, str]:
    """Resolves a city name to lat, lon, and a short display name."""
    clean_city = city.lower().strip()

    # Fast-track Ottawa to save an API call and retain the "YOW" tag
    if clean_city in ["ottawa", "yow", ""]:
        return "45.403", "-75.687", "YOW"

    url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=10&format=json"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Ottobot Mesh Node)"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))

            if "results" in data and len(data["results"]) > 0:
                # Filter specifically for Canadian cities to prevent grabbing Jamaica/UK
                ca_results = [
                    r
                    for r in data["results"]
                    if r.get("country_code", "").upper() == "CA"
                ]

                # Default to the global top result if no Canadian city exists
                res = ca_results[0] if ca_results else data["results"][0]

                lat = str(round(res["latitude"], 3))
                lon = str(round(res["longitude"], 3))

                # Format a clean, shortened display name for the mesh radio
                display_name = res["name"].upper()
                if len(display_name) > 12:
                    display_name = display_name[:12].strip()

                return lat, lon, display_name
    except Exception:
        pass

    # Fallback if geocoding fails
    return None, None, city[:12].upper()


def summarize_forecast_text(text: str) -> str:
    """Compresses a detailed forecast block using moderate abbreviation."""
    # Strip leading temperature prefixes and trailing UV index sentences
    clean = re.sub(r"^-?\d+°?\s*C\.?\s*", "", text).strip()
    clean = re.sub(r"UV index.*?(\.|$)", "", clean, flags=re.IGNORECASE)

    # Strip wind details to save characters
    clean = re.sub(r",?\s*Wind\s+[^,.]*", "", clean, flags=re.IGNORECASE)

    # Bond temperatures tightly (e.g. "High 25" -> "H:25")
    clean = re.sub(r"\bHigh\s+(-?\d+)", r"H:\1", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bLow\s+(-?\d+)", r"L:\1", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bHumidex\s+(\d+)", r"Hx:\1", clean, flags=re.IGNORECASE)

    # Condense probability swings (e.g., 30->70% shwrs)
    clean = re.sub(
        r"(\d+)\s*percent chance of showers changing to (\d+)\s*percent chance of showers",
        r"\1->\2% showers",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(
        r"(\d+)\s*percent chance of showers", r"\1% showers", clean, flags=re.IGNORECASE
    )
    clean = re.sub(r"(\d+)\s*percent chance", r"\1%", clean, flags=re.IGNORECASE)

    # Moderate abbreviation mapping
    abbr_map = {
        r"\bchanging to\b": "->",
        r"\bbecoming\b": "->",
        r"\bRisk of a thunderstorm\b": "Risk TStorm",
        r"\bRisk of thunderstorms\b": "Risk TStorm",
        r"\bthunderstorm\b": "TStorm",
        r"\bthunderstorms\b": "TStorms",
        r"\bFog patches\b": "Fog",
        r"\bMainly cloudy\b": "M.Cloudy",
        r"\bPartly cloudy\b": "P.Cloudy",
        r"\bMostly cloudy\b": "M.Cloudy",
        r"\bA mix of sun and cloud\b": "Sun/Cloud",
        r"\bClearing\b": "Clearing",
        r"\bkm/h\b": "kph",
        r"\bgusting to\b": "gust",
        r"\bLocal amount\b": "Amount",
    }

    for k, v in abbr_map.items():
        clean = re.sub(k, v, clean, flags=re.IGNORECASE)

    # Condense punctuation entirely (removes spaces after commas)
    clean = re.sub(r"\.\s*", ",", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r"\s*,", ",", clean)
    clean = re.sub(r",\s+", ",", clean)

    # --- TEMPERATURE PRIORITY LOGIC ---
    temps = []
    for prefix in [r"H:", r"L:", r"Hx:"]:
        match = re.search(rf"{prefix}-?\d+", clean)
        if match:
            temps.append(match.group(0))
            clean = re.sub(rf"{prefix}-?\d+", "", clean)

    clean = re.sub(r",+", ",", clean)
    clean = re.sub(r"[, ]+$", "", clean)
    clean = clean.strip(", ")

    if temps:
        temp_str = ",".join(temps)
        if clean:
            clean = f"{temp_str},{clean}"
        else:
            clean = temp_str

    return clean


def fetch_forecast_datamart(city_query: str) -> tuple[str, str]:
    """Fetches and summarizes a 2-period forecast from the EC location page."""
    lat, lon, disp_name = get_coords(city_query)

    if not lat:
        return "Could not geocode location.", disp_name

    url = f"https://weather.gc.ca/en/location/index.html?coords={lat},{lon}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Ottobot Mesh Node)"}
    )

    html = ""
    last_err = None

    # Retry loop to combat EC server lag
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                html = response.read().decode("utf-8")
                break
        except Exception as e:
            last_err = e
            time.sleep(1)

    if not html:
        return f"Fetch failed after 3 attempts: {last_err}", disp_name

    try:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        text = text.replace("&deg;", "°")

        if "Detailed Forecast" in text:
            target_text = text.split("Detailed Forecast")[-1]
        else:
            target_text = text

        header_pattern = re.compile(
            r"\b(Today|Tonight|(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:\s*,\s*\d+\s*[A-Za-z]+)?)\b"
        )
        night_pattern = re.compile(r"\bNight:\b")

        matches = list(header_pattern.finditer(target_text))

        if len(matches) >= 2:
            # --- Period 1 (Tonight/Today) ---
            p1_raw = matches[0].group(1).strip()
            p1_name = "Tonight" if p1_raw == "Tonight" else p1_raw.split()[0]

            p1_text_raw = target_text[matches[0].end() : matches[1].start()].strip()
            p1_summary = summarize_forecast_text(p1_text_raw)

            # --- Period 2 (Tomorrow Daytime) ---
            p2_raw = matches[1].group(1).strip()
            p2_name = "Tonight" if p2_raw == "Tonight" else p2_raw.split()[0]

            p2_end_match = matches[2].start() if len(matches) > 2 else len(target_text)
            night_match = night_pattern.search(target_text, matches[1].end())
            if night_match and night_match.start() < p2_end_match:
                p2_end_match = night_match.start()

            p2_text_raw = target_text[matches[1].end() : p2_end_match].strip()
            p2_summary = summarize_forecast_text(p2_text_raw)

            return f"{p1_name}>{p1_summary}|{p2_name}>{p2_summary}", disp_name

        return "Could not extract forecast text.", disp_name

    except Exception as e:
        return f"Parse failed: {e}", disp_name


@command("forecast", help="Get the short multi-day forecast for a city")
async def forecast(ctx: Context) -> str:
    """The async command handler triggered by !forecast."""
    who = ctx.sender_name or "you"

    # Parse city argument or default to Ottawa
    query_raw = (
        "".join(str(a) for a in ctx.args).strip()
        if hasattr(ctx, "args") and ctx.args
        else ""
    )
    query = query_raw if query_raw else "Ottawa"

    try:
        forecast_text, disp_name = await asyncio.to_thread(
            fetch_forecast_datamart, query
        )

        msg_prefix = f"@[{who}] {disp_name} FCST"

        # Check if the fetch failed before splitting
        if (
            "Could not extract" in forecast_text
            or "failed" in forecast_text
            or "Could not geocode" in forecast_text
        ):
            return f"{msg_prefix}: {forecast_text}"

        periods = forecast_text.split("|")
        single_msg = f"{msg_prefix}: " + " | ".join(periods)

        # If it fits within the limit, return as one standard message
        if len(single_msg) <= 141:
            return single_msg

        # If it exceeds the limit and we have two valid periods, join them with a newline
        if len(periods) == 2:
            msg_1 = f"{msg_prefix} 1/2: {periods[0]}"
            msg_2 = f"{msg_prefix} 2/2: {periods[1]}"

            # Truncate each individual message as a failsafe
            if len(msg_1) > 141:
                msg_1 = msg_1[:138] + "..."
            if len(msg_2) > 141:
                msg_2 = msg_2[:138] + "..."

            return f"{msg_1}\n{msg_2}"

    except Exception:
        return f"@[{who}] Error fetching forecast for {query}."

    # Failsafe truncation if logic drops through
    if len(single_msg) > 141:
        single_msg = single_msg[:138] + "..."

    return single_msg
