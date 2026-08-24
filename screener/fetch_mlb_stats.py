import io
import os
import zipfile
import logging
import requests
import pandas as pd
from screener.cache import get_cached, save_cache

logger = logging.getLogger(__name__)

MLB_STATS_CACHE_TTL_HOURS = 12  # MLB plays every day, including day games — a shorter cache than the once-a-week sports
MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

# Free historical MLB odds with both sides correctly attributed (unlike the equivalent
# NHL search, which turned up a "favorite's price only" dataset) -- 2012-2021, one row
# per team per game, moneyline/runLine/total essentially complete.
KAGGLE_MLB_ODDS_DATASET = "christophertreasure/major-league-baseball-vegas-data"
KAGGLE_MLB_ODDS_FILE = "oddsDataMLB.csv"


def _kaggle_token():
    token = os.getenv("KAGGLE_API_TOKEN")
    if not token:
        raise EnvironmentError("KAGGLE_API_TOKEN must be set in .env")
    return token


def get_mlb_historical_odds(seasons):
    """
    Fetch real historical MLB moneyline/run-line/total odds (both sides correctly
    attributed) for the given season(s) via a free Kaggle dataset, with caching. Only
    2012-2021 is available -- any other requested year is skipped rather than failing
    the whole fetch.
    """
    cache_key = f"mlb_historical_odds_{'-'.join(str(y) for y in seasons)}"
    cached = get_cached(cache_key, ttl_hours=24 * 30)  # static/historical -- never changes
    if cached is not None:
        return pd.DataFrame(cached)

    resp = requests.get(
        f"https://www.kaggle.com/api/v1/datasets/download/{KAGGLE_MLB_ODDS_DATASET}/{KAGGLE_MLB_ODDS_FILE}",
        headers={"Authorization": f"Bearer {_kaggle_token()}"},
        timeout=30,
    )
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        with zf.open(KAGGLE_MLB_ODDS_FILE) as f:
            df = pd.read_csv(f)
    df = df[df["season"].isin(seasons)]

    if df.empty:
        raise RuntimeError(f"No historical MLB odds available for any of {seasons} (only 2012-2021 is covered)")

    save_cache(cache_key, df.to_dict(orient="records"))
    return df


def get_mlb_team_map():
    """{full team name: abbreviation} for the current season's 30 MLB teams, built live
    from MLB's own API rather than hardcoded — same reasoning as the CFB team map, and
    safer than a static table given MLB teams do occasionally rename/relocate (the
    Athletics dropped "Oakland" and moved in 2025)."""
    cache_key = "mlb_team_map"
    cached = get_cached(cache_key, ttl_hours=24 * 7)
    if cached is not None:
        return cached

    resp = requests.get(f"{MLB_API_BASE}/teams", params={"sportId": 1}, timeout=15)
    resp.raise_for_status()
    teams = resp.json()["teams"]
    mapping = {t["name"]: t["abbreviation"] for t in teams}
    save_cache(cache_key, mapping)
    return mapping


def get_mlb_schedule(seasons):
    """
    Fetch the full MLB schedule (every game, with final score and venue) for the given
    season(s), with caching. One API call per season covers the whole regular season.
    """
    cache_key = f"mlb_schedule_{'-'.join(str(y) for y in seasons)}"
    cached = get_cached(cache_key, MLB_STATS_CACHE_TTL_HOURS)
    if cached is not None:
        return pd.DataFrame(cached)

    rows = []
    for season in seasons:
        try:
            logger.info(f"Fetching MLB schedule for {season}...")
            resp = requests.get(
                f"{MLB_API_BASE}/schedule",
                params={"sportId": 1, "season": season, "gameType": "R", "hydrate": "venue,probablePitcher"},
                timeout=30,
            )
            resp.raise_for_status()
            dates = resp.json().get("dates", [])
        except Exception as e:
            logger.warning(f"No MLB schedule available yet for {season}: {e}")
            continue

        for date in dates:
            for g in date.get("games", []):
                if g.get("status", {}).get("codedGameState") not in ("F", "O"):
                    home_score = away_score = None
                else:
                    home_score = g["teams"]["home"].get("score")
                    away_score = g["teams"]["away"].get("score")
                home_pitcher = g["teams"]["home"].get("probablePitcher") or {}
                away_pitcher = g["teams"]["away"].get("probablePitcher") or {}
                rows.append({
                    "game_pk": g["gamePk"],
                    "season": season,
                    "game_date": g.get("officialDate"),
                    "game_datetime_utc": g.get("gameDate"),
                    "home_team": g["teams"]["home"]["team"]["name"],
                    "away_team": g["teams"]["away"]["team"]["name"],
                    "home_score": home_score,
                    "away_score": away_score,
                    "venue_id": g.get("venue", {}).get("id"),
                    "venue_name": g.get("venue", {}).get("name"),
                    "home_pitcher_id": home_pitcher.get("id"),
                    "home_pitcher_name": home_pitcher.get("fullName"),
                    "away_pitcher_id": away_pitcher.get("id"),
                    "away_pitcher_name": away_pitcher.get("fullName"),
                })

    if not rows:
        raise RuntimeError(f"No MLB schedule data available for any of {seasons}")

    df = pd.DataFrame(rows)

    # A postponed game appears twice under the same game_pk: once as a placeholder on
    # its original date (no score) and again on its actual makeup date (with the real
    # score) -- found in production testing 2026-08-23 (56 such pairs in the 2026
    # season alone). Keep whichever row actually has a score.
    df = df.sort_values("home_score", na_position="last").drop_duplicates("game_pk", keep="first")
    df = df.sort_values(["season", "game_date"]).reset_index(drop=True)

    save_cache(cache_key, df.to_dict(orient="records"))
    return df


def get_pitcher_game_log(pitcher_id, season):
    """
    Fetch one pitcher's per-appearance stats (innings pitched, earned runs, etc.) for a
    season, with caching. Used to build each starter's own opponent-adjusted rating.
    """
    cache_key = f"mlb_pitcher_log_{pitcher_id}_{season}"
    cached = get_cached(cache_key, MLB_STATS_CACHE_TTL_HOURS)
    if cached is not None:
        return pd.DataFrame(cached)

    resp = requests.get(
        f"{MLB_API_BASE}/people/{pitcher_id}/stats",
        params={"stats": "gameLog", "group": "pitching", "season": season},
        timeout=15,
    )
    resp.raise_for_status()
    splits = resp.json().get("stats", [{}])[0].get("splits", [])

    rows = []
    for s in splits:
        stat = s.get("stat", {})
        if stat.get("gamesStarted", 0) != 1:
            continue  # only starts — relief appearances aren't relevant to a starter rating
        innings_pitched = float(stat.get("inningsPitched", 0) or 0)
        rows.append({
            "pitcher_id": pitcher_id,
            "game_pk": s.get("game", {}).get("gamePk"),
            "date": s.get("date"),
            "team": s.get("team", {}).get("name"),
            "opponent": s.get("opponent", {}).get("name"),
            "innings_pitched": innings_pitched,
            "earned_runs": stat.get("earnedRuns", 0),
            "home_away": s.get("isHome"),
        })

    df = pd.DataFrame(rows)
    save_cache(cache_key, df.to_dict(orient="records"))
    return df


def get_pitcher_game_logs_for_schedule(schedule_df):
    """
    Fetch game logs for every unique starting pitcher who appears in `schedule_df`
    (its home_pitcher_id/away_pitcher_id columns) across all seasons present. One call
    per unique pitcher-season — a few hundred for a full historical backtest, but each
    pitcher's whole season comes back in one call, so this is far cheaper than fetching
    a boxscore per game (2,000+ games/season) would be. Uses "probable pitcher" as the
    historical starter of record; a late scratch could occasionally mean this isn't
    exactly who ended up pitching, a minor simplification accepted elsewhere in this
    project too (e.g. assumed odds where a real price isn't available).
    """
    pairs = set()
    for _, row in schedule_df.iterrows():
        if pd.notna(row.get("home_pitcher_id")):
            pairs.add((int(row["home_pitcher_id"]), int(row["season"])))
        if pd.notna(row.get("away_pitcher_id")):
            pairs.add((int(row["away_pitcher_id"]), int(row["season"])))

    logger.info(f"Fetching game logs for {len(pairs)} pitcher-seasons...")
    frames = []
    for pitcher_id, season in pairs:
        try:
            frames.append(get_pitcher_game_log(pitcher_id, season))
        except Exception as e:
            logger.debug(f"Failed to fetch game log for pitcher {pitcher_id}, {season}: {e}")

    if not frames:
        return pd.DataFrame(columns=["pitcher_id", "game_pk", "date", "team", "opponent", "innings_pitched", "earned_runs", "home_away"])
    return pd.concat(frames, ignore_index=True)
