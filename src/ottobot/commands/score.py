# ==============================================================================
# VERSION: 0.5.26
# LINES CHANGED: ~35 lines modified
# CHANGELOG:
# - Incremented patch version to 0.5.26.
# - Updated "No upcoming games scheduled" strings across all fetchers to
#   dynamically inject the `max_days` parameter. The bot will now explicitly
#   state "No games in next 30 days" (or 7 days, or 365 days) to provide
#   transparency on the exact API search window.
# - Cleaned up `fetch_city_slate` and `fetch_gceazy_slate` compression
#   filters to catch the new string format.
#
# *** DEPLOYMENT NOTE FOR BOT HOSTS ***
# This module requires the `curl-cffi` package to bypass WAF blocks (403 errors)
# on the ESPN APIs. If the bot crashes on startup with:
# "ModuleNotFoundError: No module named 'curl_cffi'"
# You must update the host environment to install the new dependency:
# - If running locally via uv: Run `uv sync`
# - If running via Docker: Run `docker compose up -d --build`
# ==============================================================================

"""!score — get live sports scores and game slates via real-time APIs."""

import urllib.request
import urllib.parse
import json
import asyncio
import gzip
import re
from datetime import datetime, timedelta, date
from typing import TypedDict
from ottobot import Context, command

# Required for TLS fingerprint spoofing to bypass ESPN API 403 Forbidden blocks.
# Host must run `uv sync` or rebuild Docker to install this dependency.
from curl_cffi import requests as cffi_requests


class CFLEvent(TypedDict):
    month: int
    day: int
    time_str: str
    away: str
    home: str
    away_name: str
    home_name: str
    away_score: int
    home_score: int
    status: str
    dt: datetime


class HockeyTechEvent(TypedDict):
    game_date: date
    month: int
    day: int
    time_str: str
    away: str
    home: str
    away_name: str
    home_name: str
    away_score: str
    home_score: str
    status: str
    dt: datetime


# League Router Map
LEAGUE_MAP = {
    # ESPN Standard Leagues
    "mlb": ("espn", "baseball", "mlb", "MLB"),
    "nhl": ("espn", "hockey", "nhl", "NHL"),
    "nba": ("espn", "basketball", "nba", "NBA"),
    "nfl": ("espn", "football", "nfl", "NFL"),
    "wnba": ("espn", "basketball", "wnba", "WNBA"),
    "pwhl": ("espn", "hockey", "pwhl", "PWHL"),
    "mls": ("espn", "soccer", "usa.1", "MLS"),
    "epl": ("espn", "soccer", "eng.1", "EPL"),
    "laliga": ("espn", "soccer", "esp.1", "La Liga"),
    "la liga": ("espn", "soccer", "esp.1", "La Liga"),
    "bundesliga": ("espn", "soccer", "ger.1", "Bundesliga"),
    # Combat Sports
    "ufc": ("ufc", "mma", "ufc", "UFC"),
    "mma": ("ufc", "mma", "ufc", "UFC"),
    # Custom Dynamic JSON Fetcher
    "cfl": ("ha_cfl", "cfl", "CFL"),
    # HockeyTech (LeagueStat) Feeds
    "ohl": ("hockeytech", "ohl", "OHL"),
    "chl": (
        "hockeytech",
        "ohl",
        "OHL",
    ),  # Defaulting generic CHL queries to OHL feed for local teams
    "whl": ("hockeytech", "whl", "WHL"),
    "qmjhl": ("hockeytech", "qmjhl", "QMJHL"),
    # Paused Minor Leagues (No open APIs available)
    "cebl": ("disabled", "", "CEBL"),
    "cpl": ("disabled", "", "CPL"),
    "nll": ("disabled", "", "NLL"),
}

HELP_LEAGUES = "Bundesliga, CFL, CHL, EPL, F1, La Liga, MLB, MLS, NBA, NFL, NHL, OHL, PWHL, QMJHL, UFC, WHL, WNBA"

# Ambiguous triggers
AMBIGUOUS_MAP = {
    "giants": "Try using: ny giants or sf giants",
    "jets": "Try using: ny jets or nhl jets",
    "rangers": "Try using: nhl rangers, texas (rangers), or kitchener (rangers)",
    "kings": "Try using: nba kings or nhl kings",
    "cardinals": "Try using: nfl cardinals or stl cardinals",
    "atletico": "Try using: atletico madrid or atletico ottawa",
    "lions": "Try using: bc lions or detroit lions",
}

# Priority team sorting rules for league-wide queries
PRIORITY_TEAMS = {
    "mlb": [["tor", "blue jays", "jays"]],
    "nhl": [
        ["ott", "senators", "sens"],
        ["mtl", "canadiens", "habs"],
        ["tor", "maple leafs", "leafs"],
    ],
    "nba": [["tor", "raptors", "raps"]],
    "nfl": [["buf", "bills"]],
    "ohl": [["ott", "ottawa", "67s"]],
    "pwhl": [
        ["ott", "ottawa", "charge"],
        ["mtl", "montreal", "victoire"],
        ["tor", "toronto", "sceptres"],
    ],
    "wnba": [["tor", "toronto", "tempo"]],
}

ENG_SOCCER_SLUGS = "eng.1,eng.2,eng.3,eng.4,eng.fa,eng.league_cup"
ESP_SOCCER_SLUGS = "esp.1,esp.2,esp.copa_del_rey,esp.super_cup"

# Team Router Map
TEAM_MAP = {
    # === BASEBALL (MLB) ===
    "jays": ("espn", "baseball", "mlb", "Blue Jays", "MLB"),
    "blue jays": ("espn", "baseball", "mlb", "Blue Jays", "MLB"),
    "toronto blue jays": ("espn", "baseball", "mlb", "Blue Jays", "MLB"),
    "orioles": ("espn", "baseball", "mlb", "Orioles", "MLB"),
    "os": ("espn", "baseball", "mlb", "Orioles", "MLB"),
    "baltimore": ("espn", "baseball", "mlb", "Orioles", "MLB"),
    "red sox": ("espn", "baseball", "mlb", "Red Sox", "MLB"),
    "boston": ("espn", "baseball", "mlb", "Red Sox", "MLB"),
    "yankees": ("espn", "baseball", "mlb", "Yankees", "MLB"),
    "nyy": ("espn", "baseball", "mlb", "Yankees", "MLB"),
    "rays": ("espn", "baseball", "mlb", "Rays", "MLB"),
    "tampa bay": ("espn", "baseball", "mlb", "Rays", "MLB"),
    "white sox": ("espn", "baseball", "mlb", "White Sox", "MLB"),
    "chicago white sox": ("espn", "baseball", "mlb", "White Sox", "MLB"),
    "guardians": ("espn", "baseball", "mlb", "Guardians", "MLB"),
    "cleveland": ("espn", "baseball", "mlb", "Guardians", "MLB"),
    "tigers": ("espn", "baseball", "mlb", "Tigers", "MLB"),
    "detroit": ("espn", "baseball", "mlb", "Tigers", "MLB"),
    "royals": ("espn", "baseball", "mlb", "Royals", "MLB"),
    "kc": ("espn", "baseball", "mlb", "Royals", "MLB"),
    "twins": ("espn", "baseball", "mlb", "Twins", "MLB"),
    "minnesota": ("espn", "baseball", "mlb", "Twins", "MLB"),
    "astros": ("espn", "baseball", "mlb", "Astros", "MLB"),
    "houston": ("espn", "baseball", "mlb", "Astros", "MLB"),
    "angels": ("espn", "baseball", "mlb", "Angels", "MLB"),
    "laa": ("espn", "baseball", "mlb", "Angels", "MLB"),
    "athletics": ("espn", "baseball", "mlb", "Athletics", "MLB"),
    "as": ("espn", "baseball", "mlb", "Athletics", "MLB"),
    "mariners": ("espn", "baseball", "mlb", "Mariners", "MLB"),
    "seattle": ("espn", "baseball", "mlb", "Mariners", "MLB"),
    "texas": ("espn", "baseball", "mlb", "Rangers", "MLB"),
    "texas rangers": ("espn", "baseball", "mlb", "Rangers", "MLB"),
    "braves": ("espn", "baseball", "mlb", "Braves", "MLB"),
    "atlanta": ("espn", "baseball", "mlb", "Braves", "MLB"),
    "marlins": ("espn", "baseball", "mlb", "Marlins", "MLB"),
    "miami": ("espn", "baseball", "mlb", "Marlins", "MLB"),
    "mets": ("espn", "baseball", "mlb", "Mets", "MLB"),
    "nym": ("espn", "baseball", "mlb", "Mets", "MLB"),
    "phillies": ("espn", "baseball", "mlb", "Phillies", "MLB"),
    "philly": ("espn", "baseball", "mlb", "Phillies", "MLB"),
    "nationals": ("espn", "baseball", "mlb", "Nationals", "MLB"),
    "nats": ("espn", "baseball", "mlb", "Nationals", "MLB"),
    "cubs": ("espn", "baseball", "mlb", "Cubs", "MLB"),
    "chicago cubs": ("espn", "baseball", "mlb", "Cubs", "MLB"),
    "reds": ("espn", "baseball", "mlb", "Reds", "MLB"),
    "cincinnati": ("espn", "baseball", "mlb", "Reds", "MLB"),
    "brewers": ("espn", "baseball", "mlb", "Brewers", "MLB"),
    "milwaukee": ("espn", "baseball", "mlb", "Brewers", "MLB"),
    "pirates": ("espn", "baseball", "mlb", "Pirates", "MLB"),
    "pittsburgh": ("espn", "baseball", "mlb", "Pirates", "MLB"),
    "stl cardinals": ("espn", "baseball", "mlb", "Cardinals", "MLB"),
    "mlb cards": ("espn", "baseball", "mlb", "Cardinals", "MLB"),
    "diamondbacks": ("espn", "baseball", "mlb", "Diamondbacks", "MLB"),
    "dbacks": ("espn", "baseball", "mlb", "Diamondbacks", "MLB"),
    "rockies": ("espn", "baseball", "mlb", "Rockies", "MLB"),
    "colorado": ("espn", "baseball", "mlb", "Rockies", "MLB"),
    "dodgers": ("espn", "baseball", "mlb", "Dodgers", "MLB"),
    "lad": ("espn", "baseball", "mlb", "Dodgers", "MLB"),
    "padres": ("espn", "baseball", "mlb", "Padres", "MLB"),
    "san diego": ("espn", "baseball", "mlb", "Padres", "MLB"),
    "sf giants": ("espn", "baseball", "mlb", "Giants", "MLB"),
    "mlb giants": ("espn", "baseball", "mlb", "Giants", "MLB"),
    # === HOCKEY (NHL) ===
    "sens": ("espn", "hockey", "nhl", "Senators", "NHL"),
    "senators": ("espn", "hockey", "nhl", "Senators", "NHL"),
    "ottawa senators": ("espn", "hockey", "nhl", "Senators", "NHL"),
    "leafs": ("espn", "hockey", "nhl", "Maple Leafs", "NHL"),
    "maple leafs": ("espn", "hockey", "nhl", "Maple Leafs", "NHL"),
    "toronto maple leafs": ("espn", "hockey", "nhl", "Maple Leafs", "NHL"),
    "leafs suck": ("espn", "hockey", "nhl", "Maple Leafs", "NHL"),
    "habs": ("espn", "hockey", "nhl", "Canadiens", "NHL"),
    "canadiens": ("espn", "hockey", "nhl", "Canadiens", "NHL"),
    "montreal": ("espn", "hockey", "nhl", "Canadiens", "NHL"),
    "bruins": ("espn", "hockey", "nhl", "Bruins", "NHL"),
    "sabres": ("espn", "hockey", "nhl", "Sabres", "NHL"),
    "red wings": ("espn", "hockey", "nhl", "Red Wings", "NHL"),
    "panthers": ("espn", "hockey", "nhl", "Panthers", "NHL"),
    "nhl panthers": ("espn", "hockey", "nhl", "Panthers", "NHL"),
    "lightning": ("espn", "hockey", "nhl", "Lightning", "NHL"),
    "bolts": ("espn", "hockey", "nhl", "Lightning", "NHL"),
    "hurricanes": ("espn", "hockey", "nhl", "Hurricanes", "NHL"),
    "canes": ("espn", "hockey", "nhl", "Hurricanes", "NHL"),
    "blue jackets": ("espn", "hockey", "nhl", "Blue Jackets", "NHL"),
    "devils": ("espn", "hockey", "nhl", "Devils", "NHL"),
    "islanders": ("espn", "hockey", "nhl", "Islanders", "NHL"),
    "isles": ("espn", "hockey", "nhl", "Islanders", "NHL"),
    "nhl rangers": ("espn", "hockey", "nhl", "Rangers", "NHL"),
    "flyers": ("espn", "hockey", "nhl", "Flyers", "NHL"),
    "penguins": ("espn", "hockey", "nhl", "Penguins", "NHL"),
    "pens": ("espn", "hockey", "nhl", "Penguins", "NHL"),
    "capitals": ("espn", "hockey", "nhl", "Capitals", "NHL"),
    "caps": ("espn", "hockey", "nhl", "Capitals", "NHL"),
    "blackhawks": ("espn", "hockey", "nhl", "Blackhawks", "NHL"),
    "hawks": ("espn", "hockey", "nhl", "Blackhawks", "NHL"),
    "avalanche": ("espn", "hockey", "nhl", "Avalanche", "NHL"),
    "avs": ("espn", "hockey", "nhl", "Avalanche", "NHL"),
    "stars": ("espn", "hockey", "nhl", "Stars", "NHL"),
    "wild": ("espn", "hockey", "nhl", "Wild", "NHL"),
    "predators": ("espn", "hockey", "nhl", "Predators", "NHL"),
    "preds": ("espn", "hockey", "nhl", "Predators", "NHL"),
    "nhl blues": ("espn", "hockey", "nhl", "Blues", "NHL"),
    "utah": ("espn", "hockey", "nhl", "Utah", "NHL"),
    "nhl jets": ("espn", "hockey", "nhl", "Jets", "NHL"),
    "ducks": ("espn", "hockey", "nhl", "Ducks", "NHL"),
    "flames": ("espn", "hockey", "nhl", "Flames", "NHL"),
    "oilers": ("espn", "hockey", "nhl", "Oilers", "NHL"),
    "nhl kings": ("espn", "hockey", "nhl", "Kings", "NHL"),
    "sharks": ("espn", "hockey", "nhl", "Sharks", "NHL"),
    "kraken": ("espn", "hockey", "nhl", "Kraken", "NHL"),
    "canucks": ("espn", "hockey", "nhl", "Canucks", "NHL"),
    "nucks": ("espn", "hockey", "nhl", "Canucks", "NHL"),
    "golden knights": ("espn", "hockey", "nhl", "Golden Knights", "NHL"),
    "vgas": ("espn", "hockey", "nhl", "Golden Knights", "NHL"),
    # === HOCKEY (PWHL) ===
    "charge": ("espn", "hockey", "pwhl", "Charge", "PWHL"),
    "ottawa charge": ("espn", "hockey", "pwhl", "Charge", "PWHL"),
    "sceptres": ("espn", "hockey", "pwhl", "Sceptres", "PWHL"),
    "toronto sceptres": ("espn", "hockey", "pwhl", "Sceptres", "PWHL"),
    "victoire": ("espn", "hockey", "pwhl", "Victoire", "PWHL"),
    "montreal victoire": ("espn", "hockey", "pwhl", "Victoire", "PWHL"),
    "fleet": ("espn", "hockey", "pwhl", "Fleet", "PWHL"),
    "boston fleet": ("espn", "hockey", "pwhl", "Fleet", "PWHL"),
    "frost": ("espn", "hockey", "pwhl", "Frost", "PWHL"),
    "minnesota frost": ("espn", "hockey", "pwhl", "Frost", "PWHL"),
    "sirens": ("espn", "hockey", "pwhl", "Sirens", "PWHL"),
    "new york sirens": ("espn", "hockey", "pwhl", "Sirens", "PWHL"),
    # === BASKETBALL (NBA) ===
    "raps": ("espn", "basketball", "nba", "Raptors", "NBA"),
    "raptors": ("espn", "basketball", "nba", "Raptors", "NBA"),
    "toronto raptors": ("espn", "basketball", "nba", "Raptors", "NBA"),
    "raptros": ("espn", "basketball", "nba", "Raptors", "NBA"),
    "celtics": ("espn", "basketball", "nba", "Celtics", "NBA"),
    "nets": ("espn", "basketball", "nba", "Nets", "NBA"),
    "knicks": ("espn", "basketball", "nba", "Knicks", "NBA"),
    "76ers": ("espn", "basketball", "nba", "76ers", "NBA"),
    "sixers": ("espn", "basketball", "nba", "Sixers", "NBA"),
    "bulls": ("espn", "basketball", "nba", "Bulls", "NBA"),
    "cavaliers": ("espn", "basketball", "nba", "Cavaliers", "NBA"),
    "cavs": ("espn", "basketball", "nba", "Cavaliers", "NBA"),
    "pistons": ("espn", "basketball", "nba", "Pistons", "NBA"),
    "pacers": ("espn", "basketball", "nba", "Pacers", "NBA"),
    "bucks": ("espn", "basketball", "nba", "Bucks", "NBA"),
    "hawks nba": ("espn", "basketball", "nba", "Hawks", "NBA"),
    "hornets": ("espn", "basketball", "nba", "Hornets", "NBA"),
    "heat": ("espn", "basketball", "nba", "Heat", "NBA"),
    "magic": ("espn", "basketball", "nba", "Magic", "NBA"),
    "wizards": ("espn", "basketball", "nba", "Wizards", "NBA"),
    "nuggets": ("espn", "basketball", "nba", "Nuggets", "NBA"),
    "timberwolves": ("espn", "basketball", "nba", "Timberwolves", "NBA"),
    "wolves": ("espn", "basketball", "nba", "Timberwolves", "NBA"),
    "thunder": ("espn", "basketball", "nba", "Thunder", "NBA"),
    "okc": ("espn", "basketball", "nba", "Thunder", "NBA"),
    "trail blazers": ("espn", "basketball", "nba", "Trail Blazers", "NBA"),
    "blazers": ("espn", "basketball", "nba", "Trail Blazers", "NBA"),
    "jazz": ("espn", "basketball", "nba", "Jazz", "NBA"),
    "warriors": ("espn", "basketball", "nba", "Warriors", "NBA"),
    "dubs": ("espn", "basketball", "nba", "Warriors", "NBA"),
    "clippers": ("espn", "basketball", "nba", "Clippers", "NBA"),
    "lakers": ("espn", "basketball", "nba", "Lakers", "NBA"),
    "suns": ("espn", "basketball", "nba", "Suns", "NBA"),
    "nba kings": ("espn", "basketball", "nba", "Kings", "NBA"),
    "mavericks": ("espn", "basketball", "nba", "Mavericks", "NBA"),
    "mavs": ("espn", "basketball", "nba", "Mavericks", "NBA"),
    "rockets": ("espn", "basketball", "nba", "Rockets", "NBA"),
    "grizzlies": ("espn", "basketball", "nba", "Grizzlies", "NBA"),
    "pelicans": ("espn", "basketball", "nba", "Pelicans", "NBA"),
    "spurs": ("espn", "basketball", "nba", "Spurs", "NBA"),
    # === BASKETBALL (WNBA) ===
    "aces": ("espn", "basketball", "wnba", "Aces", "WNBA"),
    "dream": ("espn", "basketball", "wnba", "Dream", "WNBA"),
    "sky": ("espn", "basketball", "wnba", "Sky", "WNBA"),
    "sun": ("espn", "basketball", "wnba", "Sun", "WNBA"),
    "fever": ("espn", "basketball", "wnba", "Fever", "WNBA"),
    "liberty": ("espn", "basketball", "wnba", "Liberty", "WNBA"),
    "sparks": ("espn", "basketball", "wnba", "Sparks", "WNBA"),
    "lynx": ("espn", "basketball", "wnba", "Lynx", "WNBA"),
    "mercury": ("espn", "basketball", "wnba", "Mercury", "WNBA"),
    "storm": ("espn", "basketball", "wnba", "Storm", "WNBA"),
    "wings": ("espn", "basketball", "wnba", "Wings", "WNBA"),
    "mystics": ("espn", "basketball", "wnba", "Mystics", "WNBA"),
    "tempo": ("espn", "basketball", "wnba", "Tempo", "WNBA"),
    "toronto tempo": ("espn", "basketball", "wnba", "Tempo", "WNBA"),
    "valkyries": ("espn", "basketball", "wnba", "Valkyries", "WNBA"),
    "golden state valkyries": ("espn", "basketball", "wnba", "Valkyries", "WNBA"),
    "fire": ("espn", "basketball", "wnba", "Fire", "WNBA"),
    "portland fire": ("espn", "basketball", "wnba", "Fire", "WNBA"),
    # === FOOTBALL (NFL) ===
    "bills": ("espn", "football", "nfl", "Bills", "NFL"),
    "buffalo": ("espn", "football", "nfl", "Bills", "NFL"),
    "dolphins": ("espn", "football", "nfl", "Dolphins", "NFL"),
    "patriots": ("espn", "football", "nfl", "Patriots", "NFL"),
    "pats": ("espn", "football", "nfl", "Patriots", "NFL"),
    "ny jets": ("espn", "football", "nfl", "Jets", "NFL"),
    "nfl jets": ("espn", "football", "nfl", "Jets", "NFL"),
    "ravens": ("espn", "football", "nfl", "Ravens", "NFL"),
    "bengals": ("espn", "football", "nfl", "Bengals", "NFL"),
    "browns": ("espn", "football", "nfl", "Browns", "NFL"),
    "steelers": ("espn", "football", "nfl", "Steelers", "NFL"),
    "texans": ("espn", "football", "nfl", "Texans", "NFL"),
    "colts": ("espn", "football", "nfl", "Colts", "NFL"),
    "jaguars": ("espn", "football", "nfl", "Jaguars", "NFL"),
    "jags": ("espn", "football", "nfl", "Jaguars", "NFL"),
    "titans nfl": ("espn", "football", "nfl", "Titans", "NFL"),
    "broncos": ("espn", "football", "nfl", "Broncos", "NFL"),
    "chiefs": ("espn", "football", "nfl", "Chiefs", "NFL"),
    "raiders": ("espn", "football", "nfl", "Raiders", "NFL"),
    "chargers": ("espn", "football", "nfl", "Chargers", "NFL"),
    "cowboys": ("espn", "football", "nfl", "Cowboys", "NFL"),
    "ny giants": ("espn", "football", "nfl", "Giants", "NFL"),
    "nfl giants": ("espn", "football", "nfl", "Giants", "NFL"),
    "eagles": ("espn", "football", "nfl", "Eagles", "NFL"),
    "commanders": ("espn", "football", "nfl", "Commanders", "NFL"),
    "bears": ("espn", "football", "nfl", "Bears", "NFL"),
    "detroit lions": ("espn", "football", "nfl", "Lions", "NFL"),
    "packers": ("espn", "football", "nfl", "Packers", "NFL"),
    "vikings": ("espn", "football", "nfl", "Vikings", "NFL"),
    "falcons": ("espn", "football", "nfl", "Falcons", "NFL"),
    "nfl panthers": ("espn", "football", "nfl", "Panthers", "NFL"),
    "saints": ("espn", "football", "nfl", "Saints", "NFL"),
    "buccaneers": ("espn", "football", "nfl", "Buccaneers", "NFL"),
    "bucs": ("espn", "football", "nfl", "Buccaneers", "NFL"),
    "nfl cardinals": ("espn", "football", "nfl", "Cardinals", "NFL"),
    "rams": ("espn", "football", "nfl", "Rams", "NFL"),
    "49ers": ("espn", "football", "nfl", "49ers", "NFL"),
    "niners": ("espn", "football", "nfl", "49ers", "NFL"),
    "seahawks": ("espn", "football", "nfl", "Seahawks", "NFL"),
    # === SOCCER (MLS) ===
    "tfc": ("espn", "soccer", "usa.1", "Toronto FC", "MLS"),
    "toronto fc": ("espn", "soccer", "usa.1", "Toronto FC", "MLS"),
    "cf montreal": ("espn", "soccer", "usa.1", "CF Montréal", "MLS"),
    "montreal impact": ("espn", "soccer", "usa.1", "CF Montréal", "MLS"),
    "whitecaps": ("espn", "soccer", "usa.1", "Whitecaps", "MLS"),
    "vancouver whitecaps": ("espn", "soccer", "usa.1", "Whitecaps", "MLS"),
    "inter miami": ("espn", "soccer", "usa.1", "Inter Miami", "MLS"),
    "miami fc": ("espn", "soccer", "usa.1", "Inter Miami", "MLS"),
    "lafc": ("espn", "soccer", "usa.1", "LAFC", "MLS"),
    "la galaxy": ("espn", "soccer", "usa.1", "LA Galaxy", "MLS"),
    "sounders": ("espn", "soccer", "usa.1", "Sounders", "MLS"),
    "seattle sounders": ("espn", "soccer", "usa.1", "Sounders", "MLS"),
    "timbers": ("espn", "soccer", "usa.1", "Timbers", "MLS"),
    "portland timbers": ("espn", "soccer", "usa.1", "Timbers", "MLS"),
    "crew": ("espn", "soccer", "usa.1", "Crew", "MLS"),
    "columbus crew": ("espn", "soccer", "usa.1", "Crew", "MLS"),
    # === SOCCER (EPL + ENG LEAGUES + CUPS) ===
    "arsenal": ("espn", "soccer", ENG_SOCCER_SLUGS, "Arsenal", "EPL"),
    "aston villa": ("espn", "soccer", ENG_SOCCER_SLUGS, "Aston Villa", "EPL"),
    "villa": ("espn", "soccer", ENG_SOCCER_SLUGS, "Aston Villa", "EPL"),
    "bournemouth": ("espn", "soccer", ENG_SOCCER_SLUGS, "Bournemouth", "EPL"),
    "brentford": ("espn", "soccer", ENG_SOCCER_SLUGS, "Brentford", "EPL"),
    "brighton": ("espn", "soccer", ENG_SOCCER_SLUGS, "Brighton", "EPL"),
    "chelsea": ("espn", "soccer", ENG_SOCCER_SLUGS, "Chelsea", "EPL"),
    "crystal palace": ("espn", "soccer", ENG_SOCCER_SLUGS, "Crystal Palace", "EPL"),
    "palace": ("espn", "soccer", ENG_SOCCER_SLUGS, "Crystal Palace", "EPL"),
    "everton": ("espn", "soccer", ENG_SOCCER_SLUGS, "Everton", "EPL"),
    "fulham": ("espn", "soccer", ENG_SOCCER_SLUGS, "Fulham", "EPL"),
    "liverpool": ("espn", "soccer", ENG_SOCCER_SLUGS, "Liverpool", "EPL"),
    "man city": ("espn", "soccer", ENG_SOCCER_SLUGS, "Manchester City", "EPL"),
    "manchester city": ("espn", "soccer", ENG_SOCCER_SLUGS, "Manchester City", "EPL"),
    "man utd": ("espn", "soccer", ENG_SOCCER_SLUGS, "Manchester United", "EPL"),
    "manchester united": (
        "espn",
        "soccer",
        ENG_SOCCER_SLUGS,
        "Manchester United",
        "EPL",
    ),
    "united": ("espn", "soccer", ENG_SOCCER_SLUGS, "Manchester United", "EPL"),
    "newcastle": ("espn", "soccer", ENG_SOCCER_SLUGS, "Newcastle", "EPL"),
    "forest": ("espn", "soccer", ENG_SOCCER_SLUGS, "Nottingham Forest", "EPL"),
    "nottm forest": ("espn", "soccer", ENG_SOCCER_SLUGS, "Nottingham Forest", "EPL"),
    "spurs epl": ("espn", "soccer", ENG_SOCCER_SLUGS, "Tottenham", "EPL"),
    "tottenham": ("espn", "soccer", ENG_SOCCER_SLUGS, "Tottenham", "EPL"),
    "west ham": ("espn", "soccer", ENG_SOCCER_SLUGS, "West Ham", "EPL"),
    "wolves": ("espn", "soccer", ENG_SOCCER_SLUGS, "Wolverhampton", "EPL"),
    "wolverhampton": ("espn", "soccer", ENG_SOCCER_SLUGS, "Wolverhampton", "EPL"),
    "leicest city": ("espn", "soccer", ENG_SOCCER_SLUGS, "Leicester City", "EPL"),
    "leicest": ("espn", "soccer", ENG_SOCCER_SLUGS, "Leicester City", "EPL"),
    "leicester city": ("espn", "soccer", ENG_SOCCER_SLUGS, "Leicester City", "EPL"),
    # === SOCCER (La Liga + ESP LEAGUES + CUPS) ===
    "alaves": ("espn", "soccer", ESP_SOCCER_SLUGS, "Alavés", "La Liga"),
    "deportivo alaves": ("espn", "soccer", ESP_SOCCER_SLUGS, "Alavés", "La Liga"),
    "athletic bilbao": ("espn", "soccer", ESP_SOCCER_SLUGS, "Athletic Club", "La Liga"),
    "athletic club": ("espn", "soccer", ESP_SOCCER_SLUGS, "Athletic Club", "La Liga"),
    "atletico madrid": (
        "espn",
        "soccer",
        ESP_SOCCER_SLUGS,
        "Atlético Madrid",
        "La Liga",
    ),
    "atleti": ("espn", "soccer", ESP_SOCCER_SLUGS, "Atlético Madrid", "La Liga"),
    "barcelona": ("espn", "soccer", ESP_SOCCER_SLUGS, "Barcelona", "La Liga"),
    "barca": ("espn", "soccer", ESP_SOCCER_SLUGS, "Barcelona", "La Liga"),
    "celta vigo": ("espn", "soccer", ESP_SOCCER_SLUGS, "Celta Vigo", "La Liga"),
    "celta": ("espn", "soccer", ESP_SOCCER_SLUGS, "Celta Vigo", "La Liga"),
    "deportivo": ("espn", "soccer", ESP_SOCCER_SLUGS, "Deportivo La Coruña", "La Liga"),
    "deportivo la coruna": (
        "espn",
        "soccer",
        ESP_SOCCER_SLUGS,
        "Deportivo La Coruña",
        "La Liga",
    ),
    "depor": ("espn", "soccer", ESP_SOCCER_SLUGS, "Deportivo La Coruña", "La Liga"),
    "elche": ("espn", "soccer", ESP_SOCCER_SLUGS, "Elche CF", "La Liga"),
    "espanyol": ("espn", "soccer", ESP_SOCCER_SLUGS, "Espanyol", "La Liga"),
    "getafe": ("espn", "soccer", ESP_SOCCER_SLUGS, "Getafe", "La Liga"),
    "levante": ("espn", "soccer", ESP_SOCCER_SLUGS, "Levante UD", "La Liga"),
    "malaga": ("espn", "soccer", ESP_SOCCER_SLUGS, "Málaga", "La Liga"),
    "osasuna": ("espn", "soccer", ESP_SOCCER_SLUGS, "Osasuna", "La Liga"),
    "racing santander": (
        "espn",
        "soccer",
        ESP_SOCCER_SLUGS,
        "Racing Santander",
        "La Liga",
    ),
    "racing": ("espn", "soccer", ESP_SOCCER_SLUGS, "Racing Santander", "La Liga"),
    "santander": ("espn", "soccer", ESP_SOCCER_SLUGS, "Racing Santander", "La Liga"),
    "rayo vallecano": ("espn", "soccer", ESP_SOCCER_SLUGS, "Rayo Vallecano", "La Liga"),
    "rayo": ("espn", "soccer", ESP_SOCCER_SLUGS, "Rayo Vallecano", "La Liga"),
    "real betis": ("espn", "soccer", ESP_SOCCER_SLUGS, "Real Betis", "La Liga"),
    "betis": ("espn", "soccer", ESP_SOCCER_SLUGS, "Real Betis", "La Liga"),
    "real madrid": ("espn", "soccer", ESP_SOCCER_SLUGS, "Real Madrid", "La Liga"),
    "madrid": ("espn", "soccer", ESP_SOCCER_SLUGS, "Real Madrid", "La Liga"),
    "real sociedad": ("espn", "soccer", ESP_SOCCER_SLUGS, "Real Sociedad", "La Liga"),
    "sociedad": ("espn", "soccer", ESP_SOCCER_SLUGS, "Real Sociedad", "La Liga"),
    "sevilla": ("espn", "soccer", ESP_SOCCER_SLUGS, "Sevilla", "La Liga"),
    "valencia": ("espn", "soccer", ESP_SOCCER_SLUGS, "Valencia", "La Liga"),
    "villarreal": ("espn", "soccer", ESP_SOCCER_SLUGS, "Villarreal", "La Liga"),
    # === SOCCER (Bundesliga) ===
    "bayern": ("espn", "soccer", "ger.1", "Bayern Munich", "Bundesliga"),
    "bayern munich": ("espn", "soccer", "ger.1", "Bayern Munich", "Bundesliga"),
    "dortmund": ("espn", "soccer", "ger.1", "Borussia Dortmund", "Bundesliga"),
    "bvb": ("espn", "soccer", "ger.1", "Borussia Dortmund", "Bundesliga"),
    "leverkusen": ("espn", "soccer", "ger.1", "Bayer Leverkusen", "Bundesliga"),
    "bayer leverkusen": ("espn", "soccer", "ger.1", "Bayer Leverkusen", "Bundesliga"),
    "leipzig": ("espn", "soccer", "ger.1", "RB Leipzig", "Bundesliga"),
    "rb leipzig": ("espn", "soccer", "ger.1", "RB Leipzig", "Bundesliga"),
    "stuttgart": ("espn", "soccer", "ger.1", "VfB Stuttgart", "Bundesliga"),
    "eintracht": ("espn", "soccer", "eng.1", "Eintracht Frankfurt", "Bundesliga"),
    # =========================================================================
    # === CFL (Dynamic JSON Tracker)                                        ===
    # =========================================================================
    "redblacks": ("ha_cfl", "cfl", "Redblacks", "CFL"),
    "ottawa redblacks": ("ha_cfl", "cfl", "Redblacks", "CFL"),
    "argos": ("ha_cfl", "cfl", "Argonauts", "CFL"),
    "toronto argonauts": ("ha_cfl", "cfl", "Argonauts", "CFL"),
    "alouettes": ("ha_cfl", "cfl", "Alouettes", "CFL"),
    "als": ("ha_cfl", "cfl", "Alouettes", "CFL"),
    "tiger-cats": ("ha_cfl", "cfl", "Tiger-Cats", "CFL"),
    "ticats": ("ha_cfl", "cfl", "Tiger-Cats", "CFL"),
    "tiger cats": ("ha_cfl", "cfl", "Tiger-Cats", "CFL"),
    "blue bombers": ("ha_cfl", "cfl", "Blue Bombers", "CFL"),
    "bombers": ("ha_cfl", "cfl", "Blue Bombers", "CFL"),
    "roughriders": ("ha_cfl", "cfl", "Roughriders", "CFL"),
    "riders": ("ha_cfl", "cfl", "Roughriders", "CFL"),
    "elks": ("ha_cfl", "cfl", "Elks", "CFL"),
    "stampeders": ("ha_cfl", "cfl", "Stampeders", "CFL"),
    "stamps": ("ha_cfl", "cfl", "Stampeders", "CFL"),
    "bc lions": ("ha_cfl", "cfl", "BC Lions", "CFL"),
    # =========================================================================
    # === HOCKEYTECH (OHL/CHL) MINOR LEAGUES                                ===
    # =========================================================================
    "67s": ("hockeytech", "ohl", "Ottawa 67's", "OHL"),
    "ottawa 67s": ("hockeytech", "ohl", "Ottawa 67's", "OHL"),
    "generals": ("hockeytech", "ohl", "Oshawa Generals", "OHL"),
    "oshawa generals": ("hockeytech", "ohl", "Oshawa Generals", "OHL"),
    "petes": ("hockeytech", "ohl", "Peterborough Petes", "OHL"),
    "peterborough petes": ("hockeytech", "ohl", "Peterborough Petes", "OHL"),
    "knights": ("hockeytech", "ohl", "London Knights", "OHL"),
    "london knights": ("hockeytech", "ohl", "London Knights", "OHL"),
    "rangers ohl": ("hockeytech", "ohl", "Kitchener Rangers", "OHL"),
    "kitchener rangers": ("hockeytech", "ohl", "Kitchener Rangers", "OHL"),
    # Paused Minor Leagues (No open APIs available)
    "blackjacks": ("disabled", "Ottawa BlackJacks", "CEBL"),
    "ottawa blackjacks": ("disabled", "Ottawa BlackJacks", "CEBL"),
    "atletico ottawa": ("disabled", "Atletico Ottawa", "CPL"),
    "black bears": ("disabled", "Ottawa Black Bears", "NLL"),
    "titans": ("disabled", "Ottawa Titans", "Frontier"),
}

OTTAWA_TEAMS = ["ottawa senators", "ottawa charge", "ottawa redblacks", "ottawa 67s"]

TORONTO_TEAMS = [
    "toronto maple leafs",
    "toronto sceptres",
    "toronto raptors",
    "toronto tempo",
    "toronto blue jays",
    "toronto argonauts",
    "toronto fc",
]


def get_local_date(iso_str: str) -> str:
    """Offsets UTC strings by -4 hours to correctly group night games into local date."""
    if not iso_str:
        return "2099-12-31"
    try:
        clean_date = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_date) - timedelta(hours=4)
        return dt.strftime("%Y-%m-%d")
    except:
        return iso_str[:10]


def get_event_priority(event: dict, league_key: str) -> int:
    """Returns a priority rank (lower = higher priority) for local interest teams."""
    tiers = PRIORITY_TEAMS.get(league_key.lower(), [])
    if not tiers:
        return 999

    comps = event.get("competitions", [])
    if not comps:
        return 999

    competitors = comps[0].get("competitors", [])
    team_names = []
    for c in competitors:
        t = c.get("team", {})
        team_names.extend(
            [
                t.get("displayName", "").lower().replace("'", ""),
                t.get("shortDisplayName", "").lower().replace("'", ""),
                t.get("name", "").lower().replace("'", ""),
                t.get("abbreviation", "").lower().replace("'", ""),
            ]
        )

    for rank, aliases in enumerate(tiers):
        for alias in aliases:
            clean_alias = alias.replace("'", "")
            if any(clean_alias in name for name in team_names):
                return rank
    return 999


async def fetch_hockeytech(
    search_team: str,
    client_code: str,
    league_label: str,
    scope: str = "default",
    return_raw: bool = False,
    max_days: int = 365,
) -> str:
    """Directly queries the HockeyTech modulekit feeds for CHL live timekeeper integration."""
    is_next_query = scope in ["next", "tomorrow"] or (
        scope == "default" and search_team
    )

    if is_next_query:
        # Pings the 'schedule' endpoint directly to bypass the scorebar's 14-day server limit,
        # seamlessly bringing the September schedule into range during the summer.
        url = f"https://lscluster.hockeytech.com/feed/index.php?feed=modulekit&view=schedule&client_code={client_code}&key=f1aa699db3d81487"
    else:
        # Standard scorebar fetch for 'live', 'today', and 'yesterday' scopes.
        url = f"https://lscluster.hockeytech.com/feed/index.php?feed=modulekit&view=scorebar&client_code={client_code}&key=f1aa699db3d81487&numberofdaysahead=5&numberofdaysback=5"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Encoding": "gzip",
            },
        )
        resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=10)
        raw_data = resp.read()
        if resp.info().get("Content-Encoding") == "gzip":
            raw_data = gzip.decompress(raw_data)
        data = json.loads(raw_data.decode("utf-8"))
    except Exception as e:
        if return_raw:
            return ""
        return f"[{league_label}] Fetch failed: {e}"

    now = datetime.now()
    today_date = now.date()
    yest_date = today_date - timedelta(days=1)
    tom_date = today_date + timedelta(days=1)
    cutoff_date = today_date + timedelta(days=max_days)

    sk = data.get("SiteKit", {})
    score_data = sk.get("Scorebar", [])
    if not score_data:
        score_data = sk.get("Schedule", [])

    games_list = []
    if isinstance(score_data, list):
        for item in score_data:
            if isinstance(item, dict) and "games" in item:
                games_list.extend(item["games"])
            elif isinstance(item, dict) and "date_played" in item:
                games_list.append(item)

    events: list[HockeyTechEvent] = []
    for g in games_list:
        date_str = str(g.get("date_played", ""))[:10]
        try:
            game_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except:
            continue

        h_code = str(g.get("home_team_code", "???")).upper()
        a_code = str(g.get("visiting_team_code", "???")).upper()
        h_name = str(g.get("home_team_name", "")).lower()
        a_name = str(g.get("visiting_team_name", "")).lower()
        h_score = str(g.get("home_goal_count", "0"))
        a_score = str(g.get("visiting_goal_count", "0"))

        status_id = str(g.get("status", "1"))
        status_txt = str(g.get("game_status", "")).lower()

        if (
            status_id in ["2", "3"]
            or "in progress" in status_txt
            or "live" in status_txt
        ):
            status = "playing"
        elif status_id == "4" or "final" in status_txt:
            status = "complete"
        else:
            status = "scheduled"

        time_str = str(g.get("scheduled_time", "TBD")).replace(" ", "").upper()

        events.append(
            {
                "game_date": game_date,
                "month": game_date.month,
                "day": game_date.day,
                "time_str": time_str,
                "away": a_code,
                "home": h_code,
                "away_name": a_name,
                "home_name": h_name,
                "away_score": a_score,
                "home_score": h_score,
                "status": status,
                "dt": datetime.combine(game_date, datetime.min.time()),
            }
        )

    if search_team:
        s_name = search_team.lower().replace("'", "").replace("é", "e")
        events = [
            e
            for e in events
            if s_name in e["away_name"].replace("'", "").replace("é", "e")
            or s_name in e["home_name"].replace("'", "").replace("é", "e")
            or s_name == e["away"].lower().replace("'", "")
            or s_name == e["home"].lower().replace("'", "")
        ]

    matches = []
    actual_scope = scope
    slate_date_str = f"{today_date.month}/{today_date.day}"

    if scope in ["default", "today"]:
        matches = [e for e in events if e["game_date"] == today_date]
        if not matches and search_team and scope == "default":
            future = [e for e in events if today_date < e["game_date"] <= cutoff_date]
            if future:
                future.sort(key=lambda x: x["game_date"])
                matches = [future[0]]
                actual_scope = "next"
    elif scope == "live":
        matches = [e for e in events if e["status"] == "playing"]
    elif scope == "tomorrow":
        matches = [e for e in events if e["game_date"] == tom_date]
        slate_date_str = f"{tom_date.month}/{tom_date.day}"
    elif scope == "yesterday":
        matches = [e for e in events if e["game_date"] == yest_date]
        slate_date_str = f"{yest_date.month}/{yest_date.day}"
    elif scope == "next":
        future = [e for e in events if today_date < e["game_date"] <= cutoff_date]
        if future:
            future.sort(key=lambda x: x["game_date"])
            if search_team:
                matches = [future[0]]
            else:
                first_dt = future[0]["game_date"]
                matches = [e for e in future if e["game_date"] == first_dt]

    if not matches:
        if return_raw:
            return ""
        subj = search_team.title() if search_team else league_label
        if scope == "live":
            return f"{'['+league_label+']' if search_team else league_label+':'} No live games{' for '+subj if search_team else ''}."
        elif scope == "today" or (scope == "default" and not search_team):
            return f"{'['+league_label+']' if search_team else league_label+':'} No games scheduled {today_date.month}/{today_date.day}{' for '+subj if search_team else ''}."
        elif scope == "tomorrow":
            return f"{'['+league_label+']' if search_team else league_label+':'} No games scheduled {tom_date.month}/{tom_date.day}{' for '+subj if search_team else ''}."
        elif scope == "yesterday":
            return f"{'['+league_label+']' if search_team else league_label+':'} No games scheduled {yest_date.month}/{yest_date.day}{' for '+subj if search_team else ''}."
        else:
            return f"{'['+league_label+']' if search_team else league_label+':'} No games in next {max_days} days{' for '+subj if search_team else ''}."

    def ht_priority(evt):
        tiers = PRIORITY_TEAMS.get(client_code.lower(), [])
        if not tiers:
            return 999
        for rank, aliases in enumerate(tiers):
            for alias in aliases:
                clean_alias = alias.replace("'", "")
                if clean_alias in evt["away_name"].replace(
                    "'", ""
                ) or clean_alias in evt["home_name"].replace("'", ""):
                    return rank
        return 999

    matches.sort(key=lambda x: (ht_priority(x), x["game_date"]))

    pairs = []
    for e in matches:
        if e["status"] == "complete":
            status_display = "F"
        elif e["status"] == "playing":
            status_display = "Live"
        else:
            if actual_scope == "next" or e["game_date"] != today_date:
                status_display = f"{e['month']}/{e['day']} {e['time_str']}"
            else:
                status_display = e["time_str"]

        if e["status"] in ["playing", "complete"]:
            game_str = f"{e['away']} {e['away_score']}-{e['home_score']} {e['home']}({status_display})"
        else:
            game_str = f"{e['away']}@{e['home']}({status_display})"

        prefix = "Next: " if actual_scope == "next" and search_team else ""
        pairs.append(f"{prefix}{game_str}")

    if search_team:
        res = f"[{league_label}] " + " | ".join(pairs)
    else:
        prefix_scope = (
            f"{league_label} NEXT: "
            if actual_scope == "next"
            else f"{league_label} {slate_date_str}: "
        )
        res = prefix_scope + " | ".join(pairs)

    if return_raw:
        return res
    return res


async def fetch_ha_cfl(
    search_team: str,
    league_label: str,
    scope: str = "default",
    return_raw: bool = False,
    max_days: int = 365,
) -> str:
    """Dynamically parses the live CFL scoreboard JSON payload."""
    url = "https://cflscoreboard.cfl.ca/json/scoreboard/rounds.json"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
        )
        resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=10)
        raw_data = resp.read()

        if resp.info().get("Content-Encoding") == "gzip":
            raw_data = gzip.decompress(raw_data)

        schedule_data = json.loads(raw_data.decode("utf-8"))
    except Exception as e:
        if return_raw:
            return ""
        return f"[{league_label}] Fetch failed: {e}"

    now = datetime.now()
    target_m = now.month
    target_d = now.day
    tom = now + timedelta(days=1)
    yest = now - timedelta(days=1)
    cutoff_dt = now + timedelta(days=max_days)

    events: list[CFLEvent] = []
    for week in schedule_data:
        for game in week.get("tournaments", []):
            date_str = game.get("date")
            if not date_str:
                continue

            try:
                dt_utc = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S")
                dt_local = dt_utc - timedelta(hours=4)
            except:
                continue

            home = game.get("homeSquad", {})
            away = game.get("awaySquad", {})

            api_status = game.get("status", "scheduled").lower()

            if api_status in ["playing", "in progress", "in-progress"]:
                status = "playing"
            elif api_status in ["complete", "final"]:
                status = "complete"
            else:
                if dt_local <= now <= dt_local + timedelta(hours=4):
                    status = "playing"
                else:
                    status = "scheduled"

            events.append(
                {
                    "month": dt_local.month,
                    "day": dt_local.day,
                    "time_str": dt_local.strftime("%I:%M%p").lstrip("0"),
                    "away": away.get("shortName", "???").upper(),
                    "home": home.get("shortName", "???").upper(),
                    "away_name": away.get("name", "").lower(),
                    "home_name": home.get("name", "").lower(),
                    "away_score": away.get("score", 0),
                    "home_score": home.get("score", 0),
                    "status": status,
                    "dt": dt_local,
                }
            )

    if search_team:
        s_name = search_team.lower().replace("'", "").replace("é", "e")
        events = [
            e
            for e in events
            if s_name in e["away_name"].replace("'", "").replace("é", "e")
            or s_name in e["home_name"].replace("'", "").replace("é", "e")
            or s_name == e["away"].lower().replace("'", "")
            or s_name == e["home"].lower().replace("'", "")
        ]

    matches: list[CFLEvent] = []
    actual_scope = scope
    slate_date_str = f"{target_m}/{target_d}"

    if scope in ["default", "today"]:
        matches = [e for e in events if e["month"] == target_m and e["day"] == target_d]
        if not matches and search_team and scope == "default":
            future = [e for e in events if now < e["dt"] <= cutoff_dt]
            if future:
                future.sort(key=lambda x: x["dt"])
                matches = [future[0]]
                actual_scope = "next"
    elif scope == "live":
        matches = [e for e in events if e["status"] == "playing"]
    elif scope == "tomorrow":
        matches = [e for e in events if e["month"] == tom.month and e["day"] == tom.day]
        slate_date_str = f"{tom.month}/{tom.day}"
    elif scope == "yesterday":
        matches = [
            e for e in events if e["month"] == yest.month and e["day"] == yest.day
        ]
        slate_date_str = f"{yest.month}/{yest.day}"
    elif scope == "next":
        future = [e for e in events if now < e["dt"] <= cutoff_dt]
        if future:
            future.sort(key=lambda x: x["dt"])
            if search_team:
                matches = [future[0]]
            else:
                first_dt = future[0]["dt"]
                matches = [
                    e
                    for e in future
                    if e["month"] == first_dt.month and e["day"] == first_dt.day
                ]

    if not matches:
        if return_raw:
            return ""
        subj = search_team.title() if search_team else league_label
        if scope == "live":
            return f"{'['+league_label+']' if search_team else league_label+':'} No live games{' for '+subj if search_team else ''}."
        elif scope == "today" or (scope == "default" and not search_team):
            return f"{'['+league_label+']' if search_team else league_label+':'} No games scheduled {target_m}/{target_d}{' for '+subj if search_team else ''}."
        elif scope == "tomorrow":
            return f"{'['+league_label+']' if search_team else league_label+':'} No games scheduled {tom.month}/{tom.day}{' for '+subj if search_team else ''}."
        elif scope == "yesterday":
            return f"{'['+league_label+']' if search_team else league_label+':'} No games scheduled {yest.month}/{yest.day}{' for '+subj if search_team else ''}."
        else:
            return f"{'['+league_label+']' if search_team else league_label+':'} No games in next {max_days} days{' for '+subj if search_team else ''}."

    pairs = []
    for e in matches:
        if e["status"] == "complete":
            status_display = "F"
        elif e["status"] == "playing":
            status_display = "Live"
        else:
            if actual_scope == "next" or (
                e["month"] != target_m or e["day"] != target_d
            ):
                status_display = f"{e['month']}/{e['day']} {e['time_str']}"
            else:
                status_display = e["time_str"]

        if e["status"] in ["playing", "complete"]:
            game_str = f"{e['away']} {e['away_score']}-{e['home_score']} {e['home']}({status_display})"
        else:
            game_str = f"{e['away']}@{e['home']}({status_display})"

        prefix = "Next: " if actual_scope == "next" and search_team else ""
        pairs.append(f"{prefix}{game_str}")

    if search_team:
        res = f"[{league_label}] " + " | ".join(pairs)
    else:
        prefix_scope = (
            f"{league_label} NEXT: "
            if actual_scope == "next"
            else f"{league_label} {slate_date_str}: "
        )
        res = prefix_scope + " | ".join(pairs)

    if return_raw:
        return res
    return res


def format_event(event: dict, team_query: str = "", scope: str = "") -> str:
    try:
        comps = event.get("competitions", [])
        if not comps:
            return ""
        comp = comps[0]

        state = comp.get("status", {}).get("type", {}).get("state", "")
        detail = comp.get("status", {}).get("type", {}).get("shortDetail", "")

        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            return ""

        home = next(
            (c for c in competitors if c.get("homeAway") == "home"), competitors[0]
        )
        away = next(
            (c for c in competitors if c.get("homeAway") == "away"), competitors[1]
        )

        h_abbr = (
            home.get("team", {})
            .get("abbreviation", home.get("team", {}).get("displayName", "???")[:3])
            .upper()
            .strip()
        )
        a_abbr = (
            away.get("team", {})
            .get("abbreviation", away.get("team", {}).get("displayName", "???")[:3])
            .upper()
            .strip()
        )

        h_score = home.get("score", "0")
        a_score = away.get("score", "0")

        if state == "in":
            return f"{a_abbr} {a_score}-{h_score} {h_abbr}"
        elif state == "post":
            if "postponed" in detail.lower() or "canceled" in detail.lower():
                return f"{a_abbr}@{h_abbr}({detail})"
            return f"{a_abbr} {a_score}-{h_score} {h_abbr}(F)"
        else:
            event_date = event.get("date", "")
            if event_date:
                try:
                    clean_date = event_date.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(clean_date) - timedelta(hours=4)

                    time_fmt = dt.strftime("%I:%M%p").lstrip("0")

                    if "TBD" in detail.upper() or dt.strftime("%H:%M") in [
                        "00:00",
                        "04:00",
                    ]:
                        time_str = "TBD"
                    else:
                        time_str = time_fmt

                    if scope in ["today", "tomorrow", "yesterday"]:
                        t = time_str
                    else:
                        t = f"{dt.month}/{dt.day} {time_str}".strip()
                except:
                    t = "TBD"
            else:
                t = "TBD"

            prefix = "Next: " if scope == "next" and team_query else ""
            return f"{prefix}{a_abbr}@{h_abbr}({t})"
    except Exception:
        return ""


def fetch_ufc_slate(scope: str = "default", max_days: int = 30) -> str:
    """Fetches UFC events and formats individual fights. Reverses fight list to ensure Main Events print first."""
    now = datetime.now()
    today_date = now.date()
    yest_date = today_date - timedelta(days=1)
    tom_date = today_date + timedelta(days=1)
    cutoff_date = today_date + timedelta(days=max_days)

    if scope == "yesterday":
        date_param = f"&dates={yest_date.strftime('%Y%m%d')}"
        slate_date_str = f"{yest_date.month}/{yest_date.day}"
    elif scope == "tomorrow":
        date_param = f"&dates={tom_date.strftime('%Y%m%d')}"
        slate_date_str = f"{tom_date.month}/{tom_date.day}"
    elif scope in ["today", "default", "live"]:
        date_param = f"&dates={today_date.strftime('%Y%m%d')}"
        slate_date_str = f"{today_date.month}/{today_date.day}"
    else:  # next
        date_param = f"&dates={today_date.strftime('%Y%m%d')}-{(today_date + timedelta(days=max_days)).strftime('%Y%m%d')}"
        slate_date_str = f"{today_date.month}/{today_date.day}"

    url = f"https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard?limit=200{date_param}"

    try:
        response = cffi_requests.get(url, impersonate="chrome110", timeout=10)
        if response.status_code == 403:
            return "UFC: Fetch failed (403 Forbidden)"
        response.raise_for_status()
        data = response.json()

        events = data.get("events", [])

        if not events:
            if scope == "live":
                return "UFC LIVE: No live events."
            elif scope in ["yesterday", "tomorrow"]:
                return f"UFC: No events {scope}."
            return f"UFC: No events in next {max_days} days."

        events.sort(key=lambda x: x.get("date", ""))

        if scope in ["next", "default"]:
            for e in events:
                if (
                    e.get("competitions", [{}])[0]
                    .get("status", {})
                    .get("type", {})
                    .get("state", "")
                    == "pre"
                ):
                    dt = datetime.fromisoformat(
                        e.get("date", "").replace("Z", "+00:00")
                    ) - timedelta(hours=4)
                    if dt.date() <= cutoff_date:
                        name = e.get("shortName", e.get("name", "UFC Event"))
                        return f"UFC NEXT: {name} ({dt.month}/{dt.day})"
                    else:
                        return f"UFC: No events in next {max_days} days."
            return f"UFC NEXT: No events in next {max_days} days."

        target_events = []
        for e in events:
            state = (
                e.get("competitions", [{}])[0]
                .get("status", {})
                .get("type", {})
                .get("state", "")
            )
            if scope == "live" and state != "in":
                continue
            if scope != "live":
                d_str = get_local_date(e.get("date", ""))
                if scope in ["default", "today"] and d_str != today_date.strftime(
                    "%Y-%m-%d"
                ):
                    continue
                if scope == "tomorrow" and d_str != tom_date.strftime("%Y-%m-%d"):
                    continue
                if scope == "yesterday" and d_str != yest_date.strftime("%Y-%m-%d"):
                    continue
            target_events.append(e)

        if not target_events:
            if scope == "live":
                return "UFC LIVE: No live events."
            if scope in ["yesterday", "tomorrow"]:
                return f"UFC: No events {scope}."
            return f"UFC: No events scheduled on {slate_date_str}."

        e = target_events[0]
        event_name = e.get("shortName", e.get("name", "UFC Event"))

        has_live = any(
            comp.get("status", {}).get("type", {}).get("state", "") == "in"
            for comp in e.get("competitions", [])
        )

        if scope == "live" or (scope in ["today", "default"] and has_live):
            prefix = "UFC LIVE"
        elif scope in ["yesterday", "tomorrow"]:
            prefix = "UFC"
        else:
            prefix = f"UFC {slate_date_str}"

        fights = []
        for comp in reversed(e.get("competitions", [])):
            comps = comp.get("competitors", [])
            if len(comps) < 2:
                continue

            def get_fighter_name(c):
                ath = c.get("athlete", {})
                name = (
                    ath.get("shortName")
                    or ath.get("lastName")
                    or ath.get("displayName", "???")
                )
                if ". " in name:
                    name = name.split(". ")[-1]
                elif " " in name and len(name.split()) > 1:
                    name = name.split()[-1]
                return name[:7].upper()

            f1, f2 = comps[0], comps[1]
            f1_name = get_fighter_name(f1)
            f2_name = get_fighter_name(f2)

            if f1.get("winner"):
                fights.append(f"W {f1_name}/{f2_name}")
            elif f2.get("winner"):
                fights.append(f"{f1_name}/{f2_name} W")
            else:
                fights.append(f"{f1_name}/{f2_name}")

        fights_str = " | ".join(fights)
        return f"{prefix}: {event_name} - {fights_str}"

    except Exception as e:
        return f"Fetch failed: {e}"


def fetch_f1_slate(scope: str = "default", max_days: int = 365) -> str:
    url = "https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard"
    now = datetime.now()
    today_date = now.date()
    cutoff_date = today_date + timedelta(days=max_days)

    try:
        response = cffi_requests.get(url, impersonate="chrome110", timeout=10)
        if response.status_code == 403:
            return "F1: Fetch failed (403 Forbidden)"
        response.raise_for_status()
        data = response.json()

        events = data.get("events", [])
        if not events:
            return "F1: No events found."
        events.sort(key=lambda x: x.get("date", ""))

        live_events = [
            e
            for e in events
            if e.get("competitions", [{}])[0]
            .get("status", {})
            .get("type", {})
            .get("state", "")
            == "in"
        ]
        if live_events:
            e = live_events[0]
            name = e.get("shortName", e.get("name", "Race"))
            comps = e.get("competitions", [{}])[0].get("competitors", [])
            top_drivers = [
                c.get("athlete", {}).get("shortName", "Driver") for c in comps[:3]
            ]
            return f"F1 LIVE: {name} - Top 3: " + ", ".join(top_drivers)

        for e in events:
            if (
                e.get("competitions", [{}])[0]
                .get("status", {})
                .get("type", {})
                .get("state", "")
                == "pre"
            ):
                dt = datetime.fromisoformat(
                    e.get("date", "").replace("Z", "+00:00")
                ) - timedelta(hours=4)
                if dt.date() <= cutoff_date:
                    name = e.get("shortName", e.get("name", "Race"))
                    return f"F1 NEXT: {name} ({dt.month}/{dt.day})"
                else:
                    return f"F1: No events in next {max_days} days."
        return f"F1: No events in next {max_days} days."
    except Exception as e:
        return f"Fetch failed: {e}"


def fetch_espn_league_slate(
    sport: str,
    league: str,
    display_name: str,
    scope: str = "default",
    max_days: int = 30,
) -> str:
    now = datetime.now()
    today_date = now.date()
    yest_date = today_date - timedelta(days=1)
    tom_date = today_date + timedelta(days=1)

    if scope == "yesterday":
        date_param = f"&dates={yest_date.strftime('%Y%m%d')}"
        slate_date_str = f"{yest_date.month}/{yest_date.day}"
    elif scope == "tomorrow":
        date_param = f"&dates={tom_date.strftime('%Y%m%d')}"
        slate_date_str = f"{tom_date.month}/{tom_date.day}"
    elif scope in ["today", "live"]:
        date_param = f"&dates={today_date.strftime('%Y%m%d')}"
        slate_date_str = f"{today_date.month}/{today_date.day}"
    else:  # default league queries ("today") or "next"
        date_param = f"&dates={today_date.strftime('%Y%m%d')}-{(today_date + timedelta(days=max_days)).strftime('%Y%m%d')}"
        slate_date_str = f"{today_date.month}/{today_date.day}"

    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?limit=200{date_param}"

    try:
        response = cffi_requests.get(url, impersonate="chrome110", timeout=10)
        if response.status_code == 403:
            return f"{display_name}: Fetch failed (403 Forbidden)"
        response.raise_for_status()
        data = response.json()

        events = data.get("events", [])

        if not events:
            if scope == "live":
                return f"{display_name}: No live games."
            if scope in ["default", "today"]:
                return f"{display_name}: No games scheduled {today_date.month}/{today_date.day}."
            return f"{display_name}: No games in next {max_days} days."

        events.sort(key=lambda x: (get_event_priority(x, league), x.get("date", "")))

        pairs = []
        if scope == "next":
            pre_events = [
                e
                for e in events
                if e.get("competitions", [{}])[0]
                .get("status", {})
                .get("type", {})
                .get("state", "")
                == "pre"
            ]
            if not pre_events:
                return f"{display_name}: No games in next {max_days} days."

            first_date = get_local_date(pre_events[0].get("date", ""))
            next_slate = [
                e for e in pre_events if get_local_date(e.get("date", "")) == first_date
            ]
            next_slate.sort(
                key=lambda x: (get_event_priority(x, league), x.get("date", ""))
            )

            for e in next_slate:
                formatted = format_event(e, scope=scope)
                if formatted:
                    pairs.append(formatted)
            return f"{display_name} NEXT: " + " | ".join(pairs)

        for e in events:
            state = (
                e.get("competitions", [{}])[0]
                .get("status", {})
                .get("type", {})
                .get("state", "")
            )
            if scope == "live" and state != "in":
                continue
            if scope in ["default", "today"] and get_local_date(
                e.get("date", "")
            ) != today_date.strftime("%Y-%m-%d"):
                continue

            formatted = format_event(e, scope=scope)
            if formatted:
                pairs.append(formatted)

        if not pairs:
            if scope == "live":
                return f"{display_name}: No live games."
            if scope in ["default", "today"]:
                return f"{display_name}: No games scheduled {today_date.month}/{today_date.day}."
            return f"{display_name}: No games in next {max_days} days."

        prefix_scope = (
            f"{display_name} LIVE: "
            if scope == "live"
            else f"{display_name} {slate_date_str}: "
        )
        return prefix_scope + " | ".join(pairs)
    except Exception as e:
        if "400" in str(e):
            if scope == "live":
                return f"{display_name}: No live games."
            if scope in ["default", "today"]:
                return f"{display_name}: No games scheduled {today_date.month}/{today_date.day}."
            return f"{display_name}: No games in next {max_days} days."
        return f"Fetch failed: {e}"


def fetch_espn_team(
    sport: str,
    league_input: str,
    search_name: str,
    league_label: str,
    team_query: str,
    scope: str = "default",
    return_raw=False,
    max_days: int = 30,
) -> str:
    now = datetime.now()
    today_date = now.date()
    yest_date = today_date - timedelta(days=1)
    tom_date = today_date + timedelta(days=1)
    cutoff_date = today_date + timedelta(days=max_days)

    if scope == "yesterday":
        date_param = f"&dates={yest_date.strftime('%Y%m%d')}"
    elif scope == "tomorrow":
        date_param = f"&dates={tom_date.strftime('%Y%m%d')}"
    elif scope in ["today", "live"]:
        date_param = f"&dates={today_date.strftime('%Y%m%d')}"
    else:
        date_param = f"&dates={today_date.strftime('%Y%m%d')}-{(today_date + timedelta(days=max_days)).strftime('%Y%m%d')}"

    leagues = league_input.split(",")
    all_raw_events = []

    # Check multiple leagues if provided (e.g., cross-competition or relegations)
    for l_id in leagues:
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{l_id}/scoreboard?limit=1000{date_param}"
        try:
            response = cffi_requests.get(url, impersonate="chrome110", timeout=10)
            if response.status_code == 403:
                if return_raw:
                    return f"[{league_label}] {search_name.title()}: API 403 Forbidden"
                return f"[{league_label}] Fetch failed: HTTP Error 403: Forbidden"
            response.raise_for_status()
            events = response.json().get("events", [])
            all_raw_events.extend(events)
        except Exception as e:
            continue

    team_events = []
    seen_event_ids = set()
    s_name = search_name.lower().replace("'", "").replace("é", "e")

    for e in all_raw_events:
        eid = e.get("id")
        if eid and eid in seen_event_ids:
            continue
        comps = e.get("competitions", [])
        if not comps:
            continue
        competitors = comps[0].get("competitors", [])
        if len(competitors) < 2:
            continue

        c0, c1 = competitors[0].get("team", {}), competitors[1].get("team", {})
        c0_names = [
            c0.get("displayName", "").lower().replace("'", ""),
            c0.get("shortDisplayName", "").lower().replace("'", ""),
            c0.get("name", "").lower().replace("'", ""),
            c0.get("abbreviation", "").lower().replace("'", ""),
        ]
        c1_names = [
            c1.get("displayName", "").lower().replace("'", ""),
            c1.get("shortDisplayName", "").lower().replace("'", ""),
            c1.get("name", "").lower().replace("'", ""),
            c1.get("abbreviation", "").lower().replace("'", ""),
        ]

        if any(s_name in n for n in c0_names) or any(s_name in n for n in c1_names):
            if eid:
                seen_event_ids.add(eid)
            team_events.append(e)

    if not team_events:
        if return_raw:
            return ""
        if scope == "live":
            return f"[{league_label}] No live games for {search_name.title()}."
        elif scope == "today":
            return f"[{league_label}] No games scheduled {today_date.month}/{today_date.day} for {search_name.title()}."
        elif scope == "tomorrow":
            return f"[{league_label}] No games scheduled {tom_date.month}/{tom_date.day} for {search_name.title()}."
        elif scope == "yesterday":
            return f"[{league_label}] No games scheduled {yest_date.month}/{yest_date.day} for {search_name.title()}."
        return f"[{league_label}] No games in next {max_days} days for {search_name.title()}."

    team_events.sort(key=lambda x: x.get("date", ""))

    pre, live, post = [], [], []
    for e in team_events:
        state = (
            e.get("competitions", [{}])[0]
            .get("status", {})
            .get("type", {})
            .get("state", "")
        )
        if state == "pre":
            pre.append(e)
        elif state == "in":
            live.append(e)
        elif state == "post":
            post.append(e)

    if scope == "next":
        if live:
            fmt = format_event(live[0], team_query, scope)
            if fmt:
                return f"[{league_label}] Live Now: " + fmt
        if pre:
            next_date_str = get_local_date(pre[0].get("date", ""))
            try:
                next_date = datetime.strptime(next_date_str, "%Y-%m-%d").date()
            except:
                next_date = None
            if next_date and next_date <= cutoff_date:
                fmt = format_event(pre[0], team_query, scope)
                if fmt:
                    return f"[{league_label}] " + fmt
        if return_raw:
            return ""
        return f"[{league_label}] No games in next {max_days} days for {search_name.title()}."

    msg_parts = []

    if scope in ["default", "today", "live"]:
        if live:
            fmt = format_event(live[0], team_query, scope)
            if fmt:
                msg_parts.append(fmt)
        elif scope != "live":
            today_games = [
                g
                for g in post + pre
                if get_local_date(g.get("date", "")) == today_date.strftime("%Y-%m-%d")
            ]
            if today_games:
                fmt = format_event(today_games[-1], team_query, scope)
                if fmt:
                    msg_parts.append(fmt)
            elif scope == "default" and pre:
                next_date_str = get_local_date(pre[0].get("date", ""))
                try:
                    next_date = datetime.strptime(next_date_str, "%Y-%m-%d").date()
                except:
                    next_date = None
                if next_date and next_date <= cutoff_date:
                    fmt = format_event(pre[0], team_query, "next")
                    if fmt:
                        msg_parts.append(fmt)
    elif scope == "tomorrow":
        tom_games = [
            g
            for g in team_events
            if get_local_date(g.get("date", "")) == tom_date.strftime("%Y-%m-%d")
        ]
        if tom_games:
            fmt = format_event(tom_games[0], team_query, scope)
            if fmt:
                msg_parts.append(fmt)
    elif scope == "yesterday":
        yest_games = [
            g
            for g in team_events
            if get_local_date(g.get("date", "")) == yest_date.strftime("%Y-%m-%d")
        ]
        if yest_games:
            fmt = format_event(yest_games[0], team_query, scope)
            if fmt:
                msg_parts.append(fmt)

    if msg_parts:
        return f"[{league_label}] " + " | ".join(msg_parts)

    if return_raw:
        return ""
    if scope == "live":
        return f"[{league_label}] No live games for {search_name.title()}."
    elif scope == "today":
        return f"[{league_label}] No games scheduled {today_date.month}/{today_date.day} for {search_name.title()}."
    elif scope == "tomorrow":
        return f"[{league_label}] No games scheduled {tom_date.month}/{tom_date.day} for {search_name.title()}."
    elif scope == "yesterday":
        return f"[{league_label}] No games scheduled {yest_date.month}/{yest_date.day} for {search_name.title()}."
    return (
        f"[{league_label}] No games in next {max_days} days for {search_name.title()}."
    )


async def fetch_city_slate(city_query: str, scope: str) -> str:
    teams = (
        OTTAWA_TEAMS
        if city_query == "ottawa"
        else TORONTO_TEAMS if city_query == "toronto" else []
    )
    results = []

    for team_key in teams:
        lookup = TEAM_MAP.get(team_key)
        if not lookup:
            continue
        try:
            source = lookup[0]
            if source == "espn":
                res = await asyncio.to_thread(
                    fetch_espn_team,
                    lookup[1],
                    lookup[2],
                    lookup[3],
                    lookup[4],
                    team_key,
                    scope,
                    True,
                    7,
                )
            elif source == "ha_cfl":
                res = await fetch_ha_cfl(lookup[2], lookup[3], scope, True, 7)
            elif source == "hockeytech":
                res = await fetch_hockeytech(
                    lookup[2], lookup[1], lookup[3], scope, True, 7
                )
            elif source == "disabled":
                continue
            if (
                res
                and "No games" not in res
                and "No live" not in res
                and "No events" not in res
            ):
                results.append(res)
        except:
            continue

    display_scope = "" if scope == "default" else f" {scope.title()}"
    city_prefix = "YOW" if city_query == "ottawa" else city_query.title()

    if not results:
        if scope == "live":
            return f"{city_prefix} Live: No live games."
        return f"{city_prefix}{display_scope}: No games scheduled."

    return f"{city_prefix}{display_scope}: " + " | ".join(results)


async def fetch_gceazy_slate(scope: str) -> str:
    results = []

    # 1. Raptors (NBA)
    try:
        res = await asyncio.to_thread(
            fetch_espn_team,
            "basketball",
            "nba",
            "Raptors",
            "NBA",
            "toronto raptors",
            scope,
            True,
            7,
        )
        if res:
            results.append(res)
    except:
        pass

    # 2. Jays (MLB)
    try:
        res = await asyncio.to_thread(
            fetch_espn_team,
            "baseball",
            "mlb",
            "Blue Jays",
            "MLB",
            "toronto blue jays",
            scope,
            True,
            7,
        )
        if res:
            results.append(res)
    except:
        pass

    # 3. Senators (NHL)
    try:
        res = await asyncio.to_thread(
            fetch_espn_team,
            "hockey",
            "nhl",
            "Senators",
            "NHL",
            "ottawa senators",
            scope,
            True,
            7,
        )
        if res:
            results.append(res)
    except:
        pass

    # 4. F1
    try:
        f1_res = await asyncio.to_thread(fetch_f1_slate, scope, 7)
        if f1_res and "No events" not in f1_res and "No live" not in f1_res:
            results.append(f1_res)
    except:
        pass

    # 5. Tempo (WNBA)
    try:
        res = await asyncio.to_thread(
            fetch_espn_team,
            "basketball",
            "wnba",
            "Tempo",
            "WNBA",
            "toronto tempo",
            scope,
            True,
            7,
        )
        if res:
            results.append(res)
    except:
        pass

    # 6. UFC
    try:
        ufc_res = await asyncio.to_thread(fetch_ufc_slate, scope, 7)
        if ufc_res and "No events" not in ufc_res and "No live" not in ufc_res:
            results.append(ufc_res)
    except:
        pass

    # 7. Leicester City (EPL)
    try:
        res = await asyncio.to_thread(
            fetch_espn_team,
            "soccer",
            ENG_SOCCER_SLUGS,
            "Leicester City",
            "EPL",
            "leicester city",
            scope,
            True,
            7,
        )
        if res:
            results.append(res)
    except:
        pass

    if not results:
        return "No events in next 7 days."

    # Compress empty API search boundaries to preserve screen real estate
    compressed_results = []
    for res in results:
        if (
            "No games in next" in res
            or "No games scheduled" in res
            or "No live games" in res
            or "No events in next" in res
        ):
            # Extract league tag and team name using simple regex or fallback logic
            m = re.match(
                r"^\[(.*?)\] (?:No games in next \d+ days for )?(.*?):? (.*)$", res
            )
            if m:
                league_tag = m.group(1)
                team_name = m.group(2).strip()
                res = f"[{league_tag}] {team_name}: No games"
            elif "F1:" in res:
                res = "F1: No events"
            elif "UFC:" in res:
                res = "UFC: No events"
        compressed_results.append(res)

    return " | ".join(compressed_results)


@command("score", help="Get sports scores or live slates")
async def score(ctx: Context) -> str:
    who = ctx.sender_name or "you"
    query = (
        "".join(str(a) for a in ctx.args).strip()
        if hasattr(ctx, "args") and ctx.args
        else ""
    )

    if not query:
        return f"@[{who}] Try !score help for commands. Leagues: {HELP_LEAGUES}"

    parts = query.lower().split()
    if parts[0] == "help":
        return f"@[{who}] !score help: [team] OR [league] [today/tomorrow/yesterday/next/live]. Leagues: {HELP_LEAGUES}"
    if parts[0] == "live" and len(parts) == 1:
        return f"@[{who}] Use format !score [league] live. Leagues: {HELP_LEAGUES}"

    scope = "default"
    league_or_team = query.lower()
    if parts[-1] in ["today", "tomorrow", "yesterday", "next", "live"]:
        scope = parts[-1]
        league_or_team = " ".join(parts[:-1]).strip()

    if not league_or_team:
        return f"@[{who}] Specify team or league."

    try:
        if league_or_team in AMBIGUOUS_MAP:
            score_text = AMBIGUOUS_MAP[league_or_team]
        elif league_or_team in ["ottawa", "toronto"]:
            score_text = await fetch_city_slate(league_or_team, scope)
        elif league_or_team == "gceazy":
            score_text = await fetch_gceazy_slate(scope)
        elif league_or_team == "f1":
            score_text = await asyncio.to_thread(fetch_f1_slate, scope)
        elif league_or_team in ["ufc", "mma"]:
            score_text = await asyncio.to_thread(fetch_ufc_slate, scope)
        elif league_or_team in LEAGUE_MAP:
            lookup = LEAGUE_MAP[league_or_team]
            if lookup[0] == "disabled":
                score_text = f"[{lookup[2]}] API access currently unavailable."
            elif lookup[0] == "espn":
                score_text = await asyncio.to_thread(
                    fetch_espn_league_slate, lookup[1], lookup[2], lookup[3], scope
                )
            elif lookup[0] == "ha_cfl":
                score_text = await fetch_ha_cfl("", lookup[2], scope, False)
            elif lookup[0] == "hockeytech":
                score_text = await fetch_hockeytech(
                    "", lookup[1], lookup[2], scope, False
                )
        elif league_or_team in TEAM_MAP:
            lookup = TEAM_MAP[league_or_team]
            if lookup[0] == "disabled":
                score_text = f"[{lookup[3]}] API access currently unavailable."
            elif lookup[0] == "espn":
                score_text = await asyncio.to_thread(
                    fetch_espn_team,
                    lookup[1],
                    lookup[2],
                    lookup[3],
                    lookup[4],
                    league_or_team,
                    scope,
                )
            elif lookup[0] == "ha_cfl":
                score_text = await fetch_ha_cfl(lookup[2], lookup[3], scope, False)
            elif lookup[0] == "hockeytech":
                score_text = await fetch_hockeytech(
                    lookup[2], lookup[1], lookup[3], scope, False
                )
        else:
            score_text = f"'{league_or_team}' not mapped. Try !score help"

        msg = f"@[{who}] {score_text}"
    except Exception:
        msg = f"@[{who}] Error fetching scores."

    if len(msg) <= 141:
        return msg

    split_idx = msg.rfind(" | ", 0, 141)
    if split_idx != -1:
        msg1 = msg[:split_idx]
        msg2_content = msg[split_idx + 3 :].strip()

        # If msg2 already has its own bracket tag or league header, carry over only the user mention
        if msg2_content.startswith("[") or re.match(
            r"^(?:[A-Z0-9]+|F1|UFC)(?:\s+[A-Z0-9]+)*:", msg2_content
        ):
            prefix = f"@[{who}] "
        else:
            m = re.match(r"^(@\[.*?\] (?:\[.*?\] |.*?: ))", msg1)
            prefix = m.group(1) if m else f"@[{who}] "

        msg2 = prefix + msg2_content

        # Failsafe truncation if the second message still breaches 141 characters
        if len(msg2) > 141:
            msg2 = msg2[:138] + "..."

        return f"{msg1}\n{msg2}"

    return msg[:138] + "..."
