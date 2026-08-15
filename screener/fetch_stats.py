import logging
import pandas as pd
from screener.cache import get_cached, save_cache

logger = logging.getLogger(__name__)

STATS_CACHE_TTL_HOURS = 20  # NFL stats only update after games, so a long cache is fine


def get_weekly_player_stats(years):
    """
    Fetch per-player, per-game stats for the given season(s) via nfl_data_py, with caching.
    A given season's file isn't published until nflverse processes it, so any requested year
    with no data yet is skipped rather than failing the whole fetch.
    """
    cache_key = f"weekly_stats_{'-'.join(str(y) for y in years)}"
    cached = get_cached(cache_key, STATS_CACHE_TTL_HOURS)
    if cached is not None:
        return pd.DataFrame(cached)

    import nfl_data_py as nfl
    frames = []
    for year in years:
        try:
            logger.info(f"Fetching weekly player stats for {year}...")
            frames.append(nfl.import_weekly_data([year]))
        except Exception as e:
            logger.warning(f"No weekly player stats available yet for {year}: {e}")

    if not frames:
        raise RuntimeError(f"No weekly player stats available for any of {years}")

    df = pd.concat(frames, ignore_index=True)
    save_cache(cache_key, df.to_dict(orient="records"))
    return df


def get_schedules(years):
    """Fetch the game schedule (matchups, weeks, teams) for the given season(s), with caching.
    Skips any requested year that isn't available yet rather than failing the whole fetch."""
    cache_key = f"schedules_{'-'.join(str(y) for y in years)}"
    cached = get_cached(cache_key, STATS_CACHE_TTL_HOURS)
    if cached is not None:
        return pd.DataFrame(cached)

    import nfl_data_py as nfl
    frames = []
    for year in years:
        try:
            logger.info(f"Fetching schedule for {year}...")
            frames.append(nfl.import_schedules([year]))
        except Exception as e:
            logger.warning(f"No schedule available yet for {year}: {e}")

    if not frames:
        raise RuntimeError(f"No schedule data available for any of {years}")

    df = pd.concat(frames, ignore_index=True)
    save_cache(cache_key, df.to_dict(orient="records"))
    return df


def build_position_stat_team_games(weekly_df, position, stat_column):
    """
    Reshape weekly player stats into one row per team per game, showing how much that
    team's players at `position` produced in `stat_column`, and how much their opponent's
    defense allowed (which is just the same number seen from the other side). Shaped
    exactly like power_ratings.build_team_games so the same opponent-adjustment math
    (built for team scoring) can be reused here for a single stat/position instead.

    Example: build_position_stat_team_games(df, "RB", "rushing_yards") gives, for every
    team and every game, how many rushing yards their running backs produced that game.
    """
    position_df = weekly_df[weekly_df["position"] == position]

    produced = (
        position_df.groupby(["recent_team", "opponent_team", "season", "week"])[stat_column]
        .sum()
        .reset_index()
        .rename(columns={"recent_team": "team", "opponent_team": "opponent", stat_column: "scored"})
    )

    # What a defense "allowed" in a game is just the opponent's production in that same
    # game, looked up from the other side of the same table.
    produced_lookup = produced.set_index(["team", "opponent", "season", "week"])["scored"]
    mirror_keys = list(zip(produced["opponent"], produced["team"], produced["season"], produced["week"]))
    produced["allowed"] = produced_lookup.reindex(mirror_keys).values

    return produced.dropna(subset=["allowed"]).sort_values(["team", "season", "week"])
