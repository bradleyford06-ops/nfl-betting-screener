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


def get_team_name_to_abbr():
    """Build a {full team name: abbreviation} lookup from nfl_data_py's team descriptions."""
    cache_key = "team_name_map"
    cached = get_cached(cache_key, ttl_hours=24 * 30)
    if cached is not None:
        return cached

    import nfl_data_py as nfl
    desc = nfl.import_team_desc()
    mapping = dict(zip(desc["team_name"], desc["team_abbr"]))
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
