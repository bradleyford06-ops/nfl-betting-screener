import os
import re
import unicodedata
import logging
from datetime import datetime
import pandas as pd
from screener.cache import get_cached, save_cache

logger = logging.getLogger(__name__)

CFB_CACHE_TTL_HOURS = 20  # college football stats/lines only update after games, same reasoning as the NFL fetch layer


def _reject_stale_year_failures(failed_years, all_years, what):
    """
    A year with no data yet is normal for the current/upcoming season, but a year fully in
    the past should always have complete data — if it failed anyway (e.g. a transient API
    outage mid-fetch), that's a real failure, not "not published yet." Found 2026-08-31: a
    cfbd outage during a multi-year fetch caused years 2018-2024 to be silently skipped
    while 2017 succeeded, and since at least one year succeeded, the resulting PARTIAL
    dataset got cached as if it were the complete one -- every rating computed from that
    cache was then silently built from one frozen, years-stale season until the cache
    naturally expired. Raises if any failed year is unambiguously historical, so a bad
    partial fetch is never mistaken for a real, complete one and cached as such.
    """
    current_year = datetime.now().year
    stale_failures = [y for y in failed_years if y < current_year]
    if stale_failures:
        raise RuntimeError(
            f"{what} failed for already-completed season(s) {sorted(stale_failures)} out of {sorted(all_years)} "
            f"— likely a transient API issue, not missing data. Not caching a partial result."
        )


def _api_client():
    """Build an authenticated cfbd API client. CFBD_API_KEY is a free key from
    collegefootballdata.com (signup required, same pattern as ODDS_API_KEY)."""
    import cfbd
    key = os.getenv("CFBD_API_KEY")
    if not key:
        raise EnvironmentError("CFBD_API_KEY must be set in .env")
    config = cfbd.Configuration(access_token=key)
    return cfbd.ApiClient(config)


def get_cfb_schedules(years):
    """
    Fetch the full FBS schedule (every FBS team's games, including their FCS opponents)
    for the given season(s), with caching. One API call per year covers the whole season —
    unlike the odds API, cfbd doesn't charge per-week. Skips any year the API has nothing
    for yet rather than failing the whole fetch, same as get_schedules in fetch_stats.py.
    """
    cache_key = f"cfb_schedules_{'-'.join(str(y) for y in years)}"
    cached = get_cached(cache_key, CFB_CACHE_TTL_HOURS)
    if cached is not None:
        return pd.DataFrame(cached)

    import cfbd
    frames = []
    failed_years = []
    with _api_client() as client:
        games_api = cfbd.GamesApi(client)
        for year in years:
            try:
                logger.info(f"Fetching FBS schedule for {year}...")
                games = games_api.get_games(year=year, classification="fbs", season_type="regular")
                frames.append(pd.DataFrame([{
                    "season": g.season,
                    "week": g.week,
                    "home_team": g.home_team,
                    "away_team": g.away_team,
                    "home_score": g.home_points,
                    "away_score": g.away_points,
                    "home_classification": str(g.home_classification).split(".")[-1].lower(),
                    "away_classification": str(g.away_classification).split(".")[-1].lower(),
                    "completed": g.completed,
                } for g in games]))
            except Exception as e:
                logger.warning(f"No FBS schedule available yet for {year}: {e}")
                failed_years.append(year)

    if not frames:
        raise RuntimeError(f"No FBS schedule data available for any of {years}")
    _reject_stale_year_failures(failed_years, years, "FBS schedule fetch")

    df = pd.concat(frames, ignore_index=True)
    save_cache(cache_key, df.to_dict(orient="records"))
    return df


def get_cfb_betting_lines(years):
    """
    Fetch real historical FBS betting lines (spread, over/under, moneyline, plus the final
    score) for the given season(s), with caching. This is what the backtest grades
    predictions against — cfbd's spread field already follows standard convention
    (negative = home team favored), same as what model.power_ratings.screen_spread expects,
    so no sign flip is needed before passing it in.
    """
    cache_key = f"cfb_lines_{'-'.join(str(y) for y in years)}"
    cached = get_cached(cache_key, CFB_CACHE_TTL_HOURS)
    if cached is not None:
        return pd.DataFrame(cached)

    import cfbd
    rows = []
    failed_years = []
    with _api_client() as client:
        betting_api = cfbd.BettingApi(client)
        for year in years:
            try:
                logger.info(f"Fetching FBS betting lines for {year}...")
                games = betting_api.get_lines(year=year, season_type="regular")
            except Exception as e:
                logger.warning(f"No FBS betting lines available yet for {year}: {e}")
                failed_years.append(year)
                continue

            for g in games:
                if not g.lines:
                    continue
                spreads = [l.spread for l in g.lines if l.spread is not None]
                totals = [l.over_under for l in g.lines if l.over_under is not None]
                home_mls = [l.home_moneyline for l in g.lines if l.home_moneyline is not None]
                away_mls = [l.away_moneyline for l in g.lines if l.away_moneyline is not None]
                if not spreads or not totals:
                    continue
                rows.append({
                    "season": g.season,
                    "week": g.week,
                    "home_team": g.home_team,
                    "away_team": g.away_team,
                    "home_score": g.home_score,
                    "away_score": g.away_score,
                    "spread_line": sum(spreads) / len(spreads),
                    "total_line": sum(totals) / len(totals),
                    "home_moneyline": sum(home_mls) / len(home_mls) if home_mls else None,
                    "away_moneyline": sum(away_mls) / len(away_mls) if away_mls else None,
                })

    if not rows:
        raise RuntimeError(f"No FBS betting lines available for any of {years}")
    _reject_stale_year_failures(failed_years, years, "FBS betting lines fetch")

    df = pd.DataFrame(rows)
    save_cache(cache_key, df.to_dict(orient="records"))
    return df


def get_cfb_sp_plus(year):
    """
    Fetch Bill Connelly's SP+ power ratings for one season, with caching. Not an input to
    our own rating model — used only as a validation check, to sanity-check our
    opponent-adjusted ratings against a well-established public system.
    """
    cache_key = f"cfb_sp_plus_{year}"
    cached = get_cached(cache_key, CFB_CACHE_TTL_HOURS)
    if cached is not None:
        return pd.DataFrame(cached)

    import cfbd
    with _api_client() as client:
        ratings_api = cfbd.RatingsApi(client)
        logger.info(f"Fetching SP+ ratings for {year}...")
        sp = ratings_api.get_sp(year=year)

    df = pd.DataFrame([{
        "year": r.year,
        "team": r.team,
        "conference": r.conference,
        "rating": r.rating,
        "ranking": r.ranking,
        "offense_rating": r.offense.rating if r.offense else None,
        "defense_rating": r.defense.rating if r.defense else None,
    } for r in sp])
    save_cache(cache_key, df.to_dict(orient="records"))
    return df


def _normalize_team_name(name):
    """
    Normalize a team name for matching across data sources: strip accents, drop
    apostrophes/periods, collapse whitespace, lowercase. Odds-side (The Odds API) and
    cfbd-side names disagree on exactly these details often enough — "San José State" vs
    "San Jose State", "Hawai'i" vs "Hawaii", "Ragin' Cajuns" vs "Ragin Cajuns" — that
    exact-string matching silently dropped several real FBS teams from screening
    entirely (found in testing 2026-08-20). Normalizing both sides before comparing is
    more robust than hand-maintaining an override per mismatch across ~134 teams.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"['’.]", "", ascii_name)
    return re.sub(r"\s+", " ", ascii_name).strip().lower()


def get_cfb_team_name_map(year):
    """
    Build a {normalized "school mascot": "School"} lookup (e.g. "tcu horned frogs" ->
    "TCU") so odds-side full team names (The Odds API's NCAAF market) can be matched to
    cfbd's school-only names. Unlike the 32-team NFL map, FBS has ~134 teams, so this is
    built live from cfbd's team list rather than hand-typed — cached aggressively since it
    almost never changes mid-season.
    """
    cache_key = f"cfb_team_name_map_{year}"
    cached = get_cached(cache_key, ttl_hours=24 * 30)
    if cached is not None:
        return cached

    import cfbd
    with _api_client() as client:
        teams_api = cfbd.TeamsApi(client)
        teams = teams_api.get_teams(year=year)

    mapping = {}
    for t in teams:
        if not t.mascot:
            continue
        mapping[_normalize_team_name(f"{t.school} {t.mascot}")] = t.school
        for alt in (t.alternate_names or []):
            mapping[_normalize_team_name(f"{alt} {t.mascot}")] = t.school

    save_cache(cache_key, mapping)
    return mapping


def to_cfb_school_name(full_team_name, name_map):
    """Look up a team's cfbd school name from its odds-side full name, falling back to
    the name itself (unmatched) with a warning, same pattern as team_map.to_abbr."""
    normalized = _normalize_team_name(full_team_name)
    if normalized in name_map:
        return name_map[normalized]
    logger.warning(f"No CFB team-name mapping found for '{full_team_name}'")
    return full_team_name
