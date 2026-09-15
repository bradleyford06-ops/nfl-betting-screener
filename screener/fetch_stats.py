import logging
import pandas as pd
from screener.cache import get_cached, save_cache

logger = logging.getLogger(__name__)

STATS_CACHE_TTL_HOURS = 20  # NFL stats only update after games, so a long cache is fine

# nflverse's own weekly player-stats release (used below) stalled after the 2024 season and
# stopped publishing -- found 2026-09-15 by checking its release timestamps directly (last
# updated May 2025). When a requested year isn't in that release, this falls back to NFL's
# own Next Gen Stats data instead of silently going without: same nflverse pipeline, same
# nfl_data_py library, but a separate release that's kept current (verified same-day). NGS
# splits passing/rushing/receiving into three files with different column names and no
# opponent field, so this maps each onto the schema the rest of the app already expects.
# Known gaps: NGS's receiving file only tracks WR/TE (no RB receiving), and its rushing
# file only tracks RB (no QB rushing) -- those specific props simply won't resolve while
# this fallback is covering a season, same as any other player with no matching data (see
# resolve_player_display_name in model/player_trends.py). Everything else PROP_STAT_MAP
# screens is fully covered: QB passing, RB rushing, WR/TE receiving.
NGS_STAT_RENAME = {
    "passing": {
        "player_gsis_id": "player_id", "player_position": "position", "team_abbr": "recent_team",
        "pass_yards": "passing_yards", "pass_touchdowns": "passing_tds",
    },
    "rushing": {
        "player_gsis_id": "player_id", "player_position": "position", "team_abbr": "recent_team",
        "rush_attempts": "carries", "rush_yards": "rushing_yards", "rush_touchdowns": "rushing_tds",
    },
    "receiving": {
        "player_gsis_id": "player_id", "player_position": "position", "team_abbr": "recent_team",
        "yards": "receiving_yards", "rec_touchdowns": "receiving_tds",
    },
}
NGS_STAT_COLUMNS = {
    "passing": ["completions", "attempts", "passing_yards", "passing_tds"],
    "rushing": ["carries", "rushing_yards", "rushing_tds"],
    "receiving": ["receptions", "targets", "receiving_yards", "receiving_tds"],
}
NGS_JOIN_KEYS = ["player_id", "player_display_name", "season", "week", "season_type"]
NGS_TEAM_KEYS = ["position", "recent_team"]


def _opponent_lookup(schedules_df, year):
    """(week, team) -> opponent for one season, built from the schedule -- NGS doesn't
    include an opponent column the way nflverse's own weekly stats does."""
    season_games = schedules_df[schedules_df["season"] == year]
    home = season_games[["week", "home_team", "away_team"]].rename(
        columns={"home_team": "team", "away_team": "opponent"})
    away = season_games[["week", "away_team", "home_team"]].rename(
        columns={"away_team": "team", "home_team": "opponent"})
    both = pd.concat([home, away], ignore_index=True)
    return both.set_index(["week", "team"])["opponent"]


def _weekly_stats_from_ngs(year, schedules_df):
    """Build a weekly_df-shaped frame for one season from Next Gen Stats -- see the module
    docstring above for why this exists and its known gaps: NGS's receiving file only
    tracks WR/TE (no RB receiving) and its rushing file only tracks RB (no QB rushing).
    Rather than assume exactly which positions each NGS file covers, each source frame is
    tagged with whether a row actually came from it, and only rows that did get their stat
    columns zero-filled -- a row with no match in a category's own file (e.g. a running
    back in the receiving file) stays unmatched (NaN), not a false real zero."""
    import nfl_data_py as nfl

    frames = []
    for stat_type, rename in NGS_STAT_RENAME.items():
        stat_df = nfl.import_ngs_data(stat_type, [year])
        stat_df = stat_df[stat_df["week"] > 0]  # week 0 is a season-to-date summary row, not a real game
        stat_df = stat_df.rename(columns=rename)
        stat_df = stat_df[NGS_JOIN_KEYS + NGS_TEAM_KEYS + NGS_STAT_COLUMNS[stat_type]].copy()
        stat_df[f"_has_{stat_type}"] = True
        frames.append(stat_df)

    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on=NGS_JOIN_KEYS, how="outer", suffixes=("", "_dup"))
        for col in NGS_TEAM_KEYS:
            dup_col = f"{col}_dup"
            merged[col] = merged[col].combine_first(merged[dup_col])
            merged = merged.drop(columns=[dup_col])

    for stat_type, cols in NGS_STAT_COLUMNS.items():
        had_category = merged[f"_has_{stat_type}"].fillna(False)
        merged.loc[had_category, cols] = merged.loc[had_category, cols].fillna(0.0)
        merged = merged.drop(columns=[f"_has_{stat_type}"])

    opponent_lookup = _opponent_lookup(schedules_df, year)
    lookup_keys = list(zip(merged["week"], merged["recent_team"]))
    merged["opponent_team"] = [opponent_lookup.get(k) for k in lookup_keys]

    return merged.dropna(subset=["opponent_team", "position"])


def get_weekly_player_stats(years):
    """
    Fetch per-player, per-game stats for the given season(s) via nfl_data_py, with caching.
    A given season's file isn't published until nflverse processes it -- when that happens,
    falls back to building the equivalent frame from Next Gen Stats instead of silently
    skipping the season (see _weekly_stats_from_ngs above for why and its one known gap).
    Any year where even that fallback fails is skipped rather than failing the whole fetch.
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
            continue
        except Exception as e:
            logger.warning(f"nflverse weekly stats not available for {year} ({e}) -- falling back to Next Gen Stats.")

        try:
            frames.append(_weekly_stats_from_ngs(year, get_schedules([year])))
        except Exception as e:
            logger.warning(f"Next Gen Stats fallback also failed for {year}: {e}")

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
