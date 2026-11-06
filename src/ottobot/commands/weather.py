# ==============================================================================
# VERSION: 0.2.5
# LINES CHANGED: ~5 lines modified
# CHANGELOG:
# - Incremented patch version to 0.2.5.
# - Updated `get_coords` return signature to `tuple[str | None, str | None, str]`
#   to resolve type-checker errors when returning `None` on failed geocoding.
# ==============================================================================

"""!weather — check the current weather conditions for Ottawa and other cities."""

import urllib.request
import urllib.parse
import asyncio
import re
import json
import time
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
                ca_results = [
                    r
                    for r in data["results"]
                    if r.get("country_code", "").upper() == "CA"
                ]
                res = ca_results[0] if ca_results else data["results"][0]

                lat = str(round(res["latitude"], 3))
                lon = str(round(res["longitude"], 3))

                display_name = res["name"].upper()
                if len(display_name) > 12:
                    display_name = display_name[:12].strip()

                return lat, lon, display_name
    except Exception:
        pass

    return None, None, city[:12].upper()


def fetch_weather_datamart(city_query: str) -> tuple[str, str]:
    """Fetches the current observed weather from the EC location page."""
    lat, lon, disp_name = get_coords(city_query)

    if not lat:
        return "Could not geocode location.", disp_name

    url = f"https://weather.gc.ca/en/location/index.html?coords={lat},{lon}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Ottobot Mesh Node)"}
    )

    html = ""
    last_err = None

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
        # Strip HTML tags and normalize spaces
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        text = text.replace("&deg;", "°")

        # Global search: Aggressive Regex that scans the entire document
        # completely bypassing headers and page structures.
        temp_match = re.search(r"Temperature:\s*(-?\d+\.?\d*°?\s*C)", text)
        cond_match = re.search(
            r"Condition:\s*(.*?)(?=\s*Pressure:|\s*Tendency:|\s*Temperature:)", text
        )
        wind_match = re.search(
            r"Wind:\s*(.*?)(?=\s*Humidex:|\s*Visibility:|\s*Date:|\s*Temperature:)",
            text,
        )
        humid_match = re.search(r"Humidity:\s*(\d+\s*%)", text)
        humidex_match = re.search(r"Humidex:\s*(\d+)", text)
        windchill_match = re.search(r"Wind Chill:\s*(-?\d+)", text)

        # Build output string only with what we found
        out_parts = []

        if temp_match and cond_match:
            out_parts.append(
                f"{temp_match.group(1).strip()} {cond_match.group(1).strip()}"
            )
        elif temp_match:
            out_parts.append(f"{temp_match.group(1).strip()}")

        if wind_match:
            out_parts.append(f"Wnd:{wind_match.group(1).strip()}")

        if humid_match:
            out_parts.append(f"Hum:{humid_match.group(1).strip()}")

        if humidex_match:
            out_parts.append(f"Hx:{humidex_match.group(1).strip()}")
        elif windchill_match:
            out_parts.append(f"WC:{windchill_match.group(1).strip()}")

        if out_parts:
            out = ", ".join(out_parts)
            # EC has a quirk where it might duplicate "Condition:" in the text
            out = out.replace("Condition:", "").strip()
            return out, disp_name
        else:
            return "Could not locate conditions block.", disp_name

    except Exception as e:
        return f"Parse failed: {e}", disp_name


@command("weather", help="Get the current weather conditions for a city")
async def weather(ctx: Context) -> str:
    """The async command handler triggered by !weather."""
    who = ctx.sender_name or "you"

    query_raw = (
        "".join(str(a) for a in ctx.args).strip()
        if hasattr(ctx, "args") and ctx.args
        else ""
    )
    query = query_raw if query_raw else "Ottawa"

    try:
        weather_text, disp_name = await asyncio.to_thread(fetch_weather_datamart, query)
        msg_prefix = f"@[{who}] {disp_name} WX"
        single_msg = f"{msg_prefix}: {weather_text}"

    except Exception:
        return f"@[{who}] Error fetching weather for {query}."

    if len(single_msg) > 141:
        single_msg = single_msg[:138] + "..."

    return single_msg
