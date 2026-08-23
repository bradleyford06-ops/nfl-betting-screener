import io
import os
import logging
import zipfile
import requests
import pandas as pd
from screener.cache import get_cached, save_cache

logger = logging.getLogger(__name__)

NHL_STATS_CACHE_TTL_HOURS = 20  # NHL scores/stats only update after games, so a long cache is fine
NHL_API_BASE = "https://api-web.nhle.com/v1"
NHL_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}  # the NHL API blocks requests with no user-agent set

# All 32 current NHL team abbreviations, used to pull each team's full-season schedule
# (the NHL API has no single "give me the whole league's schedule" endpoint).
NHL_TEAM_ABBREVIATIONS = [
    "ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL", "DET",
    "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI", "NYR", "OTT",
    "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK",
    "WSH", "WPG",
]

MONEYPUCK_BASE = "https://peter-tanner.com/moneypuck/downloads/seasonPlayersSummary"


def nhl_season_code(year):
    """The NHL API labels a season by its start/end year pair, e.g. the 2023-2024
    season is '20232024'. `year` is the season's starting year."""
    return f"{year}{year + 1}"


def get_nhl_schedule(years):
    """
    Fetch the full NHL schedule (every team's games, deduplicated) for the given
    season(s), with caching. Built from each team's own season schedule since the
    NHL API has no single whole-league schedule endpoint.
    """
    cache_key = f"nhl_schedule_{'-'.join(str(y) for y in years)}"
    cached = get_cached(cache_key, NHL_STATS_CACHE_TTL_HOURS)
    if cached is not None:
        return pd.DataFrame(cached)

    rows = {}
    for year in years:
        season = nhl_season_code(year)
        for team in NHL_TEAM_ABBREVIATIONS:
            try:
                resp = requests.get(
                    f"{NHL_API_BASE}/club-schedule-season/{team}/{season}",
                    headers=NHL_REQUEST_HEADERS, timeout=15,
                )
                resp.raise_for_status()
                games = resp.json().get("games", [])
            except Exception as e:
                logger.warning(f"Failed to fetch {team} schedule for {season}: {e}")
                continue

            for game in games:
                if game.get("gameType") != 2:
                    continue  # regular season only — skip preseason (1) and playoffs (3)
                game_id = game["id"]
                if game_id in rows:
                    continue  # already added from the other team's schedule call
                home, away = game["homeTeam"], game["awayTeam"]
                rows[game_id] = {
                    "game_id": game_id,
                    "season": year,
                    "game_date": game.get("gameDate"),
                    "home_team": home["abbrev"],
                    "away_team": away["abbrev"],
                    "home_score": home.get("score"),
                    "away_score": away.get("score"),
                    "game_state": game.get("gameState"),
                }

    if not rows:
        raise RuntimeError(f"No NHL schedule data available for any of {years}")

    df = pd.DataFrame(rows.values()).sort_values(["season", "game_date"])
    save_cache(cache_key, df.to_dict(orient="records"))
    return df


def _fetch_moneypuck_game_logs(category, year):
    """Download and parse one season's MoneyPuck game-by-game CSV (category is
    'goalies' or 'skaters'). Filtered to situation='all' — MoneyPuck splits each
    game into per-strength-state rows (5v5, powerplay, etc.); 'all' is the combined
    full-game total, which is what a team-level or whole-game prop prediction needs."""
    url = f"{MONEYPUCK_BASE}/{category}/{year}.zip"
    resp = requests.get(url, headers=NHL_REQUEST_HEADERS, timeout=30)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = next(name for name in zf.namelist() if name.endswith(".csv"))
        with zf.open(csv_name) as f:
            df = pd.read_csv(f)

    return df[df["situation"] == "all"].copy()


def get_nhl_goalie_game_logs(years):
    """
    Fetch per-goalie, per-game performance (shots against, saves, goals against) for
    the given season(s) via MoneyPuck's free bulk data, with caching. This is what the
    goalie-form layer of the power rating is built from.
    """
    cache_key = f"nhl_goalie_logs_{'-'.join(str(y) for y in years)}"
    cached = get_cached(cache_key, NHL_STATS_CACHE_TTL_HOURS)
    if cached is not None:
        return pd.DataFrame(cached)

    frames = []
    for year in years:
        try:
            logger.info(f"Fetching MoneyPuck goalie game logs for {year}...")
            frames.append(_fetch_moneypuck_game_logs("goalies", year))
        except Exception as e:
            logger.warning(f"No MoneyPuck goalie data available yet for {year}: {e}")

    if not frames:
        raise RuntimeError(f"No goalie game log data available for any of {years}")

    df = pd.concat(frames, ignore_index=True)
    save_cache(cache_key, df.to_dict(orient="records"))
    return df


# The only free source found with correctly home/away-attributed real historical NHL
# odds (moneyline, over/under) — a per-team "favorite's moneyline" alternative dataset
# exists with more seasons but discards which side was favored, making it useless for
# grading moneyline/puck-line bets. Only covers 2021-22 and 2022-23 (2,619 games), but
# every column is complete for both. No known real puck-line price by game — the puck
# line itself (+/-1.5) is graded exactly off final scores; ROI on that market alone uses
# an assumed standard price, the same kind of estimate already made in the CFB backtest.
KAGGLE_NHL_ODDS_DATASET = "michaelmallari/sportsbook-odds-on-nhl-games"
KAGGLE_NHL_ODDS_FILES = {
    2021: "sportsbook-nhl-2021-2022.csv",
    2022: "sportsbook-nhl-2022-2023.csv",
}


def _kaggle_token():
    token = os.getenv("KAGGLE_API_TOKEN")
    if not token:
        raise EnvironmentError("KAGGLE_API_TOKEN must be set in .env")
    return token


def get_nhl_historical_odds(years):
    """
    Fetch real historical NHL moneyline/total odds (with correct home/away/favorite
    attribution) for the given season(s) via a free Kaggle dataset, with caching.
    Only 2021 and 2022 (the 2021-22 and 2022-23 seasons) are available — any other
    requested year is skipped rather than failing the whole fetch.
    """
    cache_key = f"nhl_historical_odds_{'-'.join(str(y) for y in years)}"
    cached = get_cached(cache_key, ttl_hours=24 * 30)  # this dataset is static/historical — never changes
    if cached is not None:
        return pd.DataFrame(cached)

    frames = []
    for year in years:
        filename = KAGGLE_NHL_ODDS_FILES.get(year)
        if filename is None:
            logger.warning(f"No historical odds available for {year} — only {sorted(KAGGLE_NHL_ODDS_FILES)} are covered")
            continue
        resp = requests.get(
            f"https://www.kaggle.com/api/v1/datasets/download/{KAGGLE_NHL_ODDS_DATASET}/{filename}",
            headers={"Authorization": f"Bearer {_kaggle_token()}"},
            timeout=30,
        )
        resp.raise_for_status()
        frames.append(pd.read_csv(io.StringIO(resp.text)).assign(season=year))

    if not frames:
        raise RuntimeError(f"No historical NHL odds available for any of {years}")

    df = pd.concat(frames, ignore_index=True)
    save_cache(cache_key, df.to_dict(orient="records"))
    return df


def get_nhl_skater_game_logs(years):
    """
    Fetch per-skater, per-game performance (shots on goal, goals, assists, ice time) for
    the given season(s) via MoneyPuck's free bulk data, with caching. Powers the
    shots-on-goal prop trend model.
    """
    cache_key = f"nhl_skater_logs_{'-'.join(str(y) for y in years)}"
    cached = get_cached(cache_key, NHL_STATS_CACHE_TTL_HOURS)
    if cached is not None:
        return pd.DataFrame(cached)

    frames = []
    for year in years:
        try:
            logger.info(f"Fetching MoneyPuck skater game logs for {year}...")
            frames.append(_fetch_moneypuck_game_logs("skaters", year))
        except Exception as e:
            logger.warning(f"No MoneyPuck skater data available yet for {year}: {e}")

    if not frames:
        raise RuntimeError(f"No skater game log data available for any of {years}")

    df = pd.concat(frames, ignore_index=True)
    save_cache(cache_key, df.to_dict(orient="records"))
    return df
