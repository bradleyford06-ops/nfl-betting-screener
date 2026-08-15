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


def get_defense_allowed(weekly_df, position, stat_column, games_window=8):
    """
    Compute, per team, the average of `stat_column` allowed to opposing players
    at `position` over each team's last `games_window` games.

    Example: get_defense_allowed(df, "RB", "rushing_yards") tells you how many
    rushing yards each defense has been giving up to running backs recently.
    """
    position_df = weekly_df[weekly_df["position"] == position]

    # Sum the stat across all players of that position, per opponent per game
    per_game_allowed = (
        position_df.groupby(["opponent_team", "season", "week"])[stat_column]
        .sum()
        .reset_index()
    )

    # Take each defense's most recent N games and average them
    per_game_allowed = per_game_allowed.sort_values(["opponent_team", "season", "week"])
    recent_allowed = (
        per_game_allowed.groupby("opponent_team")
        .tail(games_window)
        .groupby("opponent_team")[stat_column]
        .mean()
        .reset_index()
        .rename(columns={"opponent_team": "team", stat_column: "avg_allowed"})
    )
    return recent_allowed
