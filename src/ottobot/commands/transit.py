# ==============================================================================
# VERSION: 0.1.0
# CHANGELOG: 
# - Incremented patch version to 0.0.11.
# - Fixed type check error in `get_transit_status()` by safely guarding against 
#   None values when parsing RSS XML elements.
# ==============================================================================

"""!transit — Get live OC Transpo bus and O-Train arrivals."""
import os
import json
import sqlite3
import zipfile
import io
import asyncio
import time
import re
import csv
from datetime import datetime, timedelta
import urllib.request
import xml.etree.ElementTree as ET

from ottobot import Context, command
from curl_cffi import requests as cffi_requests

# ==============================================================================
# CONFIGURATION
# ==============================================================================
DB_PATH = "octranspo_gtfs.db"
GTFS_STATIC_URL = "https://oct-gtfs-emasagcnfmcgeham.z01.azurefd.net/public-access/GTFSExport.zip"
DB_IS_BUILDING = False

# Hardcoded OC Transpo API Key
OCTRANSPO_API_KEY = "3ecee16446b74edf821a04bba73f0a85"

# Quick-entry aliases
HUB_ALIASES = {
    "tunneys": "3011",
    "tunneys eb": "3011",
    "tunneys wb": "3011",
    "hurdman": "3023",
    "hurdman a": "3023", 
    "hurdman b": "3023",
    "blair": "3027",
    "baseline": "3017",
    "fallowfield": "3062",
    "fallowfield a": "3062",
    "fallowfield b": "3062",
    "rideau": "3000",
    "greenboro": "3032",
    "bayview": "3060",
    "lees": "3024",
    "laurier": "3022"
}

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================
def get_ci(d, target_key):
    """Case-insensitive and underscore-insensitive dictionary lookup."""
    if not isinstance(d, dict): return None
    target = target_key.lower().replace("_", "")
    for k, v in d.items():
        if k.lower().replace("_", "") == target:
            return v
    return None

def csv_generator(f):
    return csv.reader(io.TextIOWrapper(f, 'utf-8-sig'))


# ==============================================================================
# BACKGROUND WORKER: GTFS STATIC DOWNLOADER
# ==============================================================================
def update_gtfs_db():
    """Downloads the official static schedule zip and converts it to a fast SQLite DB."""
    global DB_IS_BUILDING
    if DB_IS_BUILDING:
        return
    DB_IS_BUILDING = True
    print("[Transit] Downloading OC Transpo GTFS Static data...")
    
    try:
        req = urllib.request.Request(GTFS_STATIC_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            zip_data = response.read()
            
        print("[Transit] Extracting and building SQLite database (This takes ~15 seconds)...")
        with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute("DROP TABLE IF EXISTS stops")
            c.execute("CREATE TABLE stops (stop_id TEXT PRIMARY KEY, stop_code TEXT, stop_name TEXT)")
            with z.open("stops.txt") as f:
                reader = csv_generator(f)
                header = next(reader)
                id_idx, code_idx, name_idx = header.index("stop_id"), header.index("stop_code"), header.index("stop_name")
                c.executemany("INSERT INTO stops VALUES (?, ?, ?)", 
                              ((r[id_idx], r[code_idx], r[name_idx]) for r in reader if len(r) > max(id_idx, code_idx, name_idx)))
            
            c.execute("DROP TABLE IF EXISTS routes")
            c.execute("CREATE TABLE routes (route_id TEXT PRIMARY KEY, route_short_name TEXT)")
            with z.open("routes.txt") as f:
                reader = csv_generator(f)
                header = next(reader)
                id_idx, sn_idx = header.index("route_id"), header.index("route_short_name")
                c.executemany("INSERT INTO routes VALUES (?, ?)", 
                              ((r[id_idx], r[sn_idx]) for r in reader if len(r) > max(id_idx, sn_idx)))
            
            c.execute("DROP TABLE IF EXISTS trips")
            c.execute("CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT, trip_headsign TEXT)")
            with z.open("trips.txt") as f:
                reader = csv_generator(f)
                header = next(reader)
                tid_idx, rid_idx, sid_idx, hs_idx = header.index("trip_id"), header.index("route_id"), header.index("service_id"), header.index("trip_headsign")
                c.executemany("INSERT INTO trips VALUES (?, ?, ?, ?)", 
                              ((r[tid_idx], r[rid_idx], r[sid_idx], r[hs_idx]) for r in reader if len(r) > max(tid_idx, rid_idx, sid_idx, hs_idx)))
            
            c.execute("DROP TABLE IF EXISTS calendar")
            c.execute("CREATE TABLE calendar (service_id TEXT PRIMARY KEY, monday INTEGER, tuesday INTEGER, wednesday INTEGER, thursday INTEGER, friday INTEGER, saturday INTEGER, sunday INTEGER, start_date TEXT, end_date TEXT)")
            if "calendar.txt" in z.namelist():
                with z.open("calendar.txt") as f:
                    reader = csv_generator(f)
                    header = next(reader)
                    c.executemany("INSERT INTO calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                                  ((r[0], int(r[1]), int(r[2]), int(r[3]), int(r[4]), int(r[5]), int(r[6]), int(r[7]), r[8], r[9]) for r in reader))
            
            c.execute("DROP TABLE IF EXISTS calendar_dates")
            c.execute("CREATE TABLE calendar_dates (service_id TEXT, date TEXT, exception_type INTEGER)")
            if "calendar_dates.txt" in z.namelist():
                with z.open("calendar_dates.txt") as f:
                    reader = csv_generator(f)
                    header = next(reader)
                    idx_s, idx_d, idx_e = header.index("service_id"), header.index("date"), header.index("exception_type")
                    c.executemany("INSERT INTO calendar_dates VALUES (?, ?, ?)", 
                                  ((r[idx_s], r[idx_d], int(r[idx_e])) for r in reader))
            
            c.execute("DROP TABLE IF EXISTS stop_times")
            c.execute("CREATE TABLE stop_times (trip_id TEXT, arrival_time_sec INTEGER, stop_id TEXT)")
            with z.open("stop_times.txt") as f:
                reader = csv_generator(f)
                header = next(reader)
                tid_idx, arr_idx, sid_idx = header.index("trip_id"), header.index("arrival_time"), header.index("stop_id")
                
                def st_gen():
                    for r in reader:
                        if len(r) <= max(tid_idx, arr_idx, sid_idx): continue
                        try:
                            h, m, s = map(int, r[arr_idx].split(':'))
                            yield (r[tid_idx], h * 3600 + m * 60 + s, r[sid_idx])
                        except:
                            pass
                c.executemany("INSERT INTO stop_times VALUES (?, ?, ?)", st_gen())
            
            c.execute("CREATE INDEX idx_st_stop ON stop_times(stop_id)")
            c.execute("CREATE INDEX idx_st_arr ON stop_times(arrival_time_sec)")
            c.execute("CREATE INDEX idx_stops_code ON stops(stop_code)")
            
            conn.commit()
            conn.close()
        print("[Transit] GTFS Database built successfully.")
    except Exception as e:
        print(f"[Transit] Failed to build GTFS DB: {e}")
    finally:
        DB_IS_BUILDING = False


# ==============================================================================
# GTFS DATA FETCHING & PARSING
# ==============================================================================
def get_active_service_ids(cursor, target_date: datetime):
    date_str = target_date.strftime("%Y%m%d")
    weekday_str = target_date.strftime("%A").lower()
    
    query = f"""
        SELECT service_id FROM calendar 
        WHERE start_date <= ? AND end_date >= ? AND {weekday_str} = 1
        AND service_id NOT IN (SELECT service_id FROM calendar_dates WHERE date = ? AND exception_type = 2)
        UNION
        SELECT service_id FROM calendar_dates WHERE date = ? AND exception_type = 1
    """
    cursor.execute(query, (date_str, date_str, date_str, date_str))
    return [r[0] for r in cursor.fetchall()]

def fetch_rt_updates():
    if not OCTRANSPO_API_KEY:
        return {"api_error": "No Key"} 
        
    urls = [
        "https://nextrip-public-api.azure-api.net/octranspo/gtfs-rt-tp/beta/v1/TripUpdates",
        "https://nextrip-public-api.azure-api.net/octranspo/gtfs-rt-tp/beta/v1/TripUpdates?format=json",
        "https://nextrip-public-api.azure-api.net/octranspo/gtfs-rt/v1/TripUpdates?format=json"
    ]
    
    headers = {'Ocp-Apim-Subscription-Key': OCTRANSPO_API_KEY, 'Accept': 'application/json'}
    
    for url in urls:
        try:
            resp = cffi_requests.get(url, headers=headers, impersonate="chrome110", timeout=8)
            resp.raise_for_status()
            return resp.json()
        except:
            continue
                
    return {"api_error": "Fetch Failed"}

def fetch_rt_alerts(route_filter):
    """Fetches route-specific detours and service alerts."""
    if not OCTRANSPO_API_KEY:
        return "Alerts unavailable (No API Key)."
        
    urls = [
        "https://nextrip-public-api.azure-api.net/octranspo/gtfs-rt-tp/beta/v1/Alerts",
        "https://nextrip-public-api.azure-api.net/octranspo/gtfs-rt-tp/v1/Alerts?format=json",
        "https://nextrip-public-api.azure-api.net/octranspo/gtfs-rt/v1/Alerts?format=json"
    ]
    
    headers = {'Ocp-Apim-Subscription-Key': OCTRANSPO_API_KEY, 'Accept': 'application/json'}
    
    for url in urls:
        try:
            resp = cffi_requests.get(url, headers=headers, impersonate="chrome110", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                entities = get_ci(data, 'entity') or get_ci(data, 'entities') or []
                for entity in entities:
                    alert = get_ci(entity, 'alert') or {}
                    informed_entities = get_ci(alert, 'informedentity') or []
                    
                    is_target_route = False
                    for ie in informed_entities:
                        r_id = str(get_ci(ie, 'routeid') or '')
                        if r_id == route_filter:
                            is_target_route = True
                            break
                            
                    if is_target_route:
                        header = get_ci(alert, 'headertext') or {}
                        translations = get_ci(header, 'translation') or []
                        for t in translations:
                            lang = get_ci(t, 'language')
                            if lang in ('en', 'en-CA', 'en-US'):
                                return f"Route {route_filter}: {get_ci(t, 'text')}"
                        if translations:
                            return f"Route {route_filter}: {get_ci(translations[0], 'text')}"
                            
                return f"Route {route_filter}: No active alerts."
        except:
            continue
            
    return f"Route {route_filter}: No active service alerts."

def get_transit_status():
    try:
        resp = cffi_requests.get("https://www.octranspo.com/feeds/updates-en/", impersonate="chrome110", timeout=8)
        root = ET.fromstring(resp.content)
        alerts = []
        for item in root.findall('./channel/item'):
            title_elem = item.find('title')
            if title_elem is not None and title_elem.text:
                title = title_elem.text
                if 'line 1' in title.lower() or 'o-train' in title.lower() or 'r1' in title.lower():
                    alerts.append(title.strip())
                
        if not alerts:
            return "O-Train Status: Line 1 NORMAL | Line 2 CLOSED | No active major alerts."
        else:
            return "Alerts: " + " | ".join(alerts[:2])
    except:
        return "Failed to fetch transit status."


# ==============================================================================
# COMMAND EXECUTION
# ==============================================================================
@command("transit", help="Get live OC Transpo bus and O-Train arrivals")
async def transit(ctx: Context) -> str:
    who = ctx.sender_name or "you"
    
    if hasattr(ctx, 'args') and ctx.args:
        query = ctx.args.strip().lower() if isinstance(ctx.args, str) else " ".join(str(a) for a in ctx.args).strip().lower()
    else:
        query = ""
        
    args = query.split()
    
    if not args or args[0] == "help":
        msg1 = f"@[{who}] !transit [stop] | !transit [hub] [rt] | !transit alert [rt] | !transit status"
        msg2 = f"@[{who}] ETAs: '4m*' = GPS, 'CXL' = Canceled. Ex: !transit hurdman 98"
        return f"{msg1}\n{msg2}"
        
    if args[0] == "status" or args[0] == "otrain":
        status = await asyncio.to_thread(get_transit_status)
        return f"@[{who}] {status}"
        
    if args[0] == "alert" and len(args) > 1:
        route = args[1].upper()
        alert_msg = await asyncio.to_thread(fetch_rt_alerts, route)
        res = f"@[{who}] {alert_msg}"
        return res[:141] if len(res) > 141 else res
        
    # Ensure database exists before querying
    if not os.path.exists(DB_PATH):
        await asyncio.to_thread(update_gtfs_db)
        
    if DB_IS_BUILDING:
        return f"@[{who}] Transit DB is currently updating from the server. Try again in 15 seconds."

    # Parse Hub Alias & Route Filter safely
    stop_code = None
    route_filter = None
    
    if len(args) >= 2:
        composite_alias = f"{args[0]} {args[1]}"
        if composite_alias in HUB_ALIASES:
            stop_code = HUB_ALIASES[composite_alias]
            route_filter = args[2].upper() if len(args) > 2 else None

    if not stop_code:
        raw_stop = args[0]
        stop_code = HUB_ALIASES.get(raw_stop, raw_stop)
        route_filter = args[1].upper() if len(args) > 1 else None

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT stop_id, stop_name FROM stops WHERE stop_code = ?", (stop_code,))
        rows = c.fetchall()
        if not rows:
            return f"@[{who}] Stop {stop_code} not found."
            
        target_stop_ids = [r[0] for r in rows]
        
        now = datetime.now()
        if now.hour < 4:
            service_date = now - timedelta(days=1)
            current_sec = 24 * 3600 + now.hour * 3600 + now.minute * 60 + now.second
        else:
            service_date = now
            current_sec = now.hour * 3600 + now.minute * 60 + now.second
            
        active_services = get_active_service_ids(c, service_date)
        if not active_services:
            return f"@[{who}] Stop {stop_code}: No active service schedules found for today."

        c.execute(f"""
            SELECT st.trip_id, st.arrival_time_sec, r.route_short_name, t.trip_headsign
            FROM stop_times st
            JOIN trips t ON st.trip_id = t.trip_id
            JOIN routes r ON t.route_id = r.route_id
            WHERE st.stop_id IN ({','.join(['?']*len(target_stop_ids))})
            AND t.service_id IN ({','.join(['?']*len(active_services))})
            AND st.arrival_time_sec BETWEEN ? AND ?
        """, target_stop_ids + active_services + [current_sec, current_sec + 3600])
        
        scheduled_data = c.fetchall()
        
        merged = {}
        for row in scheduled_data:
            tid, arr_sec, r_name, headsign = row
            eta = (arr_sec - current_sec) // 60
            merged[tid] = {
                'route': r_name,
                'headsign': headsign,
                'eta': eta,
                'is_gps': False,
                'canceled': False
            }

        # Fetch Live GPS GTFS-RT Data
        rt_data = await asyncio.to_thread(fetch_rt_updates)
        rt_error = rt_data.get('api_error')
        
        entities = get_ci(rt_data, 'entity') or get_ci(rt_data, 'entities') or []
        rt_entities_count = len(entities)
        rt_matches_count = 0
        
        for entity in entities:
            tu = get_ci(entity, 'tripupdate') or {}
            trip = get_ci(tu, 'trip') or {}
            tid = str(get_ci(trip, 'tripid') or '')
            
            stop_updates = get_ci(tu, 'stoptimeupdate') or []
            
            for stu in stop_updates:
                stu_stop_id = str(get_ci(stu, 'stopid') or '')
                
                if stu_stop_id in target_stop_ids:
                    arr = get_ci(stu, 'arrival') or {}
                    dep = get_ci(stu, 'departure') or {}
                    
                    arr_time = get_ci(arr, 'time') or get_ci(dep, 'time')
                    arr_delay = get_ci(arr, 'delay')
                    dep_delay = get_ci(dep, 'delay')
                    delay = arr_delay if arr_delay is not None else dep_delay
                    
                    eta_rt = None
                    if arr_time:
                        eta_rt = (int(arr_time) - int(now.timestamp())) // 60
                    elif delay is not None and tid in merged:
                        eta_rt = merged[tid]['eta'] + (int(delay) // 60)
                        
                    if eta_rt is not None:
                        rt_matches_count += 1
                        is_canceled = str(get_ci(trip, 'schedulerelationship')).upper() == 'CANCELED'
                        
                        if tid in merged:
                            merged[tid]['eta'] = eta_rt
                            merged[tid]['is_gps'] = True
                            if is_canceled:
                                merged[tid]['canceled'] = True
                        else:
                            c.execute("""
                                SELECT r.route_short_name, t.trip_headsign 
                                FROM trips t JOIN routes r ON t.route_id = r.route_id 
                                WHERE t.trip_id = ?
                            """, (tid,))
                            t_info = c.fetchone()
                            if t_info:
                                merged[tid] = {
                                    'route': t_info[0],
                                    'headsign': t_info[1],
                                    'eta': eta_rt,
                                    'is_gps': True,
                                    'canceled': is_canceled
                                }

        conn.close()
        
        valid = [a for a in merged.values() if a['eta'] >= 0 or a['canceled']]
        valid.sort(key=lambda x: x['eta'])
        
        if route_filter:
            valid = [a for a in valid if a['route'] == route_filter]
            
        has_gps = any(a['is_gps'] for a in valid)
        diag_tag = ""
        if not has_gps:
            if rt_error:
                diag_tag = f" (Err: {rt_error})"
            elif rt_entities_count == 0:
                keys_str = ",".join(list(rt_data.keys())[:3])
                diag_tag = f" (RT Empty. Keys: {keys_str})"
            else:
                diag_tag = f" (RT: {rt_entities_count} buses, Matched: {rt_matches_count})"

        if not valid:
            suffix = f" for route {route_filter}" if route_filter else ""
            res = f"@[{who}] Stop {stop_code}: No upcoming arrivals{suffix}.{diag_tag}"
            return res

        parts = []
        for a in valid[:6]:
            headsign = a['headsign'][:4].title()
            if a['canceled']:
                parts.append(f"{a['route']} {headsign} CXL")
            else:
                rt_marker = "*" if a['is_gps'] else ""
                parts.append(f"{a['route']} {headsign} {a['eta']}m{rt_marker}")
            
        slate = " | ".join(parts)
        prefix = f"Stop {stop_code}: " if not route_filter else f"Stop {stop_code} [{route_filter}]: "
        
        res = f"@[{who}] {prefix}{slate}{diag_tag}"
        
        if len(res) > 141:
            split = res.rfind(" | ", 0, 141)
            if split != -1:
                return res[:split]
            return res[:138] + "..."
            
        return res

    except Exception as e:
        return f"@[{who}] Transit fetch failed: {e}"