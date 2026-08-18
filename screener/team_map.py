import logging
from screener.cache import get_cached, save_cache

logger = logging.getLogger(__name__)

# The Odds API returns full team names ("Kansas City Chiefs"); nfl_data_py uses
# short codes ("KC"). This bridges the two so both data sources can be joined.

# Manual overrides for the handful of cases where naming conventions have drifted
# (e.g. franchise rebrands) — fill in here if a lookup ever comes back empty.
MANUAL_OVERRIDES = {
    "Washington Commanders": "WAS",
    "Las Vegas Raiders": "LV",
    "Los Angeles Rams": "LA",
}

# Static fallback for all 32 teams — this data essentially never changes (the last rebrand
# was Washington in 2022), so it's safe to hardcode as a last resort if the live
# nfl_data_py fetch fails. Found in production 2026-08-17: a transient 404 from this one
# unprotected call crashed the entire scheduled run even though every other fetch in the
# pipeline already degrades gracefully — this fallback (and the try/except below) closes
# that gap without waiting on the upstream data source to be reliable.
STATIC_TEAM_NAME_TO_ABBR = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Los Angeles Rams": "LA", "Los Angeles Chargers": "LAC",
    "Las Vegas Raiders": "LV", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "Seattle Seahawks": "SEA", "San Francisco 49ers": "SF", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


def get_team_name_to_abbr():
    """
    Build a {full team name: abbreviation} lookup from nfl_data_py's team descriptions.
    Falls back to a static, hardcoded mapping (this data almost never changes) if the live
    fetch fails for any reason — this one call previously had no error handling at all and
    took down an entire scheduled run over a transient issue.
    """
    cache_key = "team_name_map"
    cached = get_cached(cache_key, ttl_hours=24 * 30)
    if cached is not None:
        return cached

    try:
        import nfl_data_py as nfl
        desc = nfl.import_team_desc()
        mapping = dict(zip(desc["team_name"], desc["team_abbr"]))
    except Exception as e:
        logger.warning(f"Live team description fetch failed, using static fallback: {e}")
        mapping = dict(STATIC_TEAM_NAME_TO_ABBR)

    mapping.update(MANUAL_OVERRIDES)
    save_cache(cache_key, mapping)
    return mapping


def to_abbr(full_team_name, name_map):
    """Look up a team's abbreviation, falling back to the manual overrides, then the name itself."""
    if full_team_name in name_map:
        return name_map[full_team_name]
    if full_team_name in MANUAL_OVERRIDES:
        return MANUAL_OVERRIDES[full_team_name]
    logger.warning(f"No abbreviation mapping found for team '{full_team_name}'")
    return full_team_name
