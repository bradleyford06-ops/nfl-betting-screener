import os
import logging
import requests
from screener.cache import get_cached, save_cache

logger = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"
CFB_SPORT = "americanfootball_ncaaf"
NHL_SPORT = "icehockey_nhl"
MLB_SPORT = "baseball_mlb"
ODDS_CACHE_TTL_HOURS = 6  # lines move during the week, but we only run a few times a week

PROP_MARKETS = [
    "player_pass_yds", "player_pass_tds", "player_pass_completions", "player_pass_attempts",
    "player_rush_yds", "player_rush_tds", "player_rush_attempts",
    "player_receptions", "player_reception_yds", "player_reception_tds",
]

# player_total_saves doubles as our starting-goalie confirmation signal — a book only
# posts a saves line for the goalie it expects to actually play, so "does this goalie
# have a posted line today" stands in for an official starter announcement.
NHL_PROP_MARKETS = ["player_shots_on_goal", "player_total_saves"]


def _api_key():
    key = os.getenv("ODDS_API_KEY")
    if not key:
        raise EnvironmentError("ODDS_API_KEY must be set in .env")
    return key


def get_events(sport=SPORT):
    """Fetch the list of upcoming games in `sport` (event IDs are needed to pull player props), with caching."""
    cache_key = f"odds_events_{sport}"
    cached = get_cached(cache_key, ODDS_CACHE_TTL_HOURS)
    if cached is not None:
        return cached

    logger.info(f"Fetching upcoming {sport} events...")
    resp = requests.get(
        f"{BASE_URL}/sports/{sport}/events",
        params={"apiKey": _api_key()},
        timeout=15,
    )
    resp.raise_for_status()
    events = resp.json()
    save_cache(cache_key, events)
    return events


def get_game_odds(regions="us", markets="h2h,spreads,totals", sport=SPORT):
    """Fetch moneyline/spread/total odds for all upcoming games in `sport`, with caching."""
    cache_key = f"odds_games_{sport}_{markets}"
    cached = get_cached(cache_key, ODDS_CACHE_TTL_HOURS)
    if cached is not None:
        return cached

    logger.info(f"Fetching game odds ({sport}, {markets})...")
    resp = requests.get(
        f"{BASE_URL}/sports/{sport}/odds",
        params={
            "apiKey": _api_key(),
            "regions": regions,
            "markets": markets,
            "oddsFormat": "american",
        },
        timeout=15,
    )
    resp.raise_for_status()
    odds = resp.json()
    save_cache(cache_key, odds)
    return odds


def get_player_props(event_id, markets=None, regions="us", sport=SPORT):
    """Fetch player prop odds for a single game (event), with caching. Costs API quota per call."""
    markets = markets or PROP_MARKETS
    markets_str = ",".join(markets)
    cache_key = f"odds_props_{sport}_{event_id}_{markets_str}"
    cached = get_cached(cache_key, ODDS_CACHE_TTL_HOURS)
    if cached is not None:
        return cached

    logger.info(f"Fetching player props for event {event_id}...")
    resp = requests.get(
        f"{BASE_URL}/sports/{sport}/events/{event_id}/odds",
        params={
            "apiKey": _api_key(),
            "regions": regions,
            "markets": markets_str,
            "oddsFormat": "american",
        },
        timeout=15,
    )
    resp.raise_for_status()
    props = resp.json()
    save_cache(cache_key, props)
    return props
