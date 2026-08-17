import logging
import pandas as pd
from screener.cache import get_cached, save_cache

logger = logging.getLogger(__name__)

PBP_CACHE_TTL_HOURS = 20  # matches the other stats caches — pbp only updates after games

# Only pull the columns the coverage-simulation model actually needs. The full play-by-play
# table has ~400 columns; nfl_data_py's own column-filtering had inconsistent behavior when
# tested (silently dropped valid columns alongside the invalid ones), so this fetches the
# full table and trims it down here instead, before it ever hits the cache.
PBP_COLUMNS = [
    "season", "week", "game_id", "posteam", "defteam",
    "play_type", "pass", "rush",
    "defense_man_zone_type", "defense_coverage_type",
    "receiver_player_id", "receiver_player_name", "receiving_yards", "complete_pass",
]


def get_play_by_play(years):
    """
    Fetch play-by-play data for the given season(s) via nfl_data_py, trimmed to just the
    columns the coverage-simulation model needs, with caching. Same year-by-year fallback
    as the other fetchers, since a season's data isn't published until nflverse processes it.

    Player identity here is `receiver_player_id` (a GSIS ID), which matches `player_id` in
    the weekly stats data exactly — use that to join, not player names (play-by-play uses
    abbreviated names like "J.Dotson" that don't match weekly data's full names).
    """
    cache_key = f"pbp_{'-'.join(str(y) for y in years)}"
    cached = get_cached(cache_key, PBP_CACHE_TTL_HOURS)
    if cached is not None:
        return pd.DataFrame(cached)

    import nfl_data_py as nfl
    frames = []
    for year in years:
        try:
            logger.info(f"Fetching play-by-play data for {year}...")
            year_pbp = nfl.import_pbp_data([year], downcast=True)
            frames.append(year_pbp[PBP_COLUMNS])
        except Exception as e:
            logger.warning(f"No play-by-play data available yet for {year}: {e}")

    if not frames:
        raise RuntimeError(f"No play-by-play data available for any of {years}")

    df = pd.concat(frames, ignore_index=True)
    save_cache(cache_key, df.to_dict(orient="records"))
    return df
