import logging
import pandas as pd
from screener.cache import get_cached, save_cache

logger = logging.getLogger(__name__)

STATS_CACHE_TTL_HOURS = 20  # NFL stats only update after games, so a long cache is fine

# nflverse's own weekly player-stats release (used below) stalled after the 2024 season and
# stopped publishing -- found 2026-09-15 by checking its release timestamps directly (last
# updated May 2025). When a requested year isn't in that release, this falls back to
# rebuilding the same box scores directly from play-by-play instead of silently going
# without: same nflverse pipeline, same nfl_data_py library, but the raw-plays release is
# actively current (confirmed 2025 and 2026 files exist, updated same-day). An earlier
# version of this fallback used NFL's Next Gen Stats data instead, since it's already
# aggregated to weekly box scores -- but NGS splits passing/rushing/receiving into three
# files that each only cover certain positions (its receiving file excludes running backs,
# its rushing file excludes quarterbacks), and patching those gaps by cross-referencing
# play-by-play anyway turned out messier than just using play-by-play as the one source.
# Raw plays are tagged by the actual player on every play regardless of position, so
# aggregating them ourselves has no such gap. Verified against NGS's own numbers for a
# category it does cover (passing) -- exact match.
PBP_STAT_AGG = {
    "passing": {
        "player_col": "passer_player_id", "attempt_col": "pass_attempt",
        "agg": {"completions": ("complete_pass", "sum"), "attempts": ("pass_attempt", "sum"),
                "passing_yards": ("passing_yards", "sum"), "passing_tds": ("pass_touchdown", "sum")},
    },
    "rushing": {
        "player_col": "rusher_player_id", "attempt_col": "rush_attempt",
        "agg": {"carries": ("rush_attempt", "sum"), "rushing_yards": ("rushing_yards", "sum"),
                "rushing_tds": ("rush_touchdown", "sum")},
    },
    "receiving": {
        "player_col": "receiver_player_id", "attempt_col": "pass_attempt",
        "agg": {"targets": ("pass_attempt", "sum"), "receptions": ("complete_pass", "sum"),
                "receiving_yards": ("receiving_yards", "sum"), "receiving_tds": ("pass_touchdown", "sum")},
    },
}


SKILL_POSITIONS = {"QB", "RB", "WR", "TE", "FB"}


def _active_skill_players(year):
    """(player_id, season, week, recent_team) for every skill-position player confirmed to
    have taken a real offensive snap that game, via snap counts -- lets _weekly_stats_from_pbp
    tell a genuinely-zero game (on the field, never targeted or handed the ball) apart from
    a player who didn't play at all. Without this, a player with a real zero-stat game (e.g.
    a TE who played 20 snaps but was never thrown to) has no row anywhere in the play-by-play
    aggregation, so a prop pick on them can never be graded even though the real answer (0,
    so an Over line loses) is knowable -- found live via Erick All and Calvin Ridley picks
    stuck open after a real week's games had already completed. Snap counts use PFR's own
    player IDs, not the GSIS IDs the rest of this file keys on, so this joins through
    nflverse's own id crosswalk (nfl.import_ids)."""
    cache_key = f"active_skill_players_{year}"
    cached = get_cached(cache_key, STATS_CACHE_TTL_HOURS)
    if cached is not None:
        return pd.DataFrame(cached)

    import nfl_data_py as nfl
    snaps = nfl.import_snap_counts([year])
    snaps = snaps[snaps["position"].isin(SKILL_POSITIONS) & (snaps["offense_snaps"] > 0)]

    id_crosswalk = nfl.import_ids()[["pfr_id", "gsis_id"]].dropna(subset=["pfr_id", "gsis_id"])
    active = snaps.merge(id_crosswalk, left_on="pfr_player_id", right_on="pfr_id", how="inner")
    active = active[["gsis_id", "season", "week", "team"]].rename(
        columns={"gsis_id": "player_id", "team": "recent_team"}).drop_duplicates()

    save_cache(cache_key, active.to_dict(orient="records"))
    return active


def _opponent_lookup(schedules_df, year):
    """(week, team) -> opponent for one season, built from the schedule -- play-by-play
    doesn't include an opponent column the way nflverse's own weekly stats does."""
    season_games = schedules_df[schedules_df["season"] == year]
    home = season_games[["week", "home_team", "away_team"]].rename(
        columns={"home_team": "team", "away_team": "opponent"})
    away = season_games[["week", "away_team", "home_team"]].rename(
        columns={"away_team": "team", "home_team": "opponent"})
    both = pd.concat([home, away], ignore_index=True)
    return both.set_index(["week", "team"])["opponent"]


def _weekly_stats_from_pbp(year, schedules_df):
    """Build a weekly_df-shaped frame for one season directly from play-by-play -- see the
    module docstring above for why. Player identity (display name, position) comes from
    nflverse's player roster table, joined on the GSIS ID play-by-play already tags every
    play with; opponent comes from the schedule the same way NGS-based reconstruction did."""
    import nfl_data_py as nfl

    pbp = nfl.import_pbp_data([year], downcast=True)

    frames = []
    for stat_type, config in PBP_STAT_AGG.items():
        stat_df = (
            pbp[pbp[config["attempt_col"]] == 1]
            .groupby([config["player_col"], "posteam", "season", "week"])
            .agg(**config["agg"])
            .reset_index()
            .rename(columns={config["player_col"]: "player_id", "posteam": "recent_team"})
        )
        frames.append(stat_df)

    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on=["player_id", "recent_team", "season", "week"], how="outer")

    # Add a row for anyone confirmed to have played but who has no row yet in any category
    # above (a real zero-stat game) -- see _active_skill_players.
    merged = merged.merge(_active_skill_players(year), on=["player_id", "recent_team", "season", "week"], how="outer")

    stat_columns = [col for config in PBP_STAT_AGG.values() for col in config["agg"]]
    merged[stat_columns] = merged[stat_columns].fillna(0.0)

    players = nfl.import_players()[["gsis_id", "display_name", "position"]].rename(
        columns={"gsis_id": "player_id", "display_name": "player_display_name"})
    merged = merged.merge(players, on="player_id", how="left").dropna(subset=["player_display_name", "position"])

    opponent_lookup = _opponent_lookup(schedules_df, year)
    lookup_keys = list(zip(merged["week"], merged["recent_team"]))
    merged["opponent_team"] = [opponent_lookup.get(k) for k in lookup_keys]

    return merged.dropna(subset=["opponent_team"])


def get_weekly_player_stats(years):
    """
    Fetch per-player, per-game stats for the given season(s) via nfl_data_py, with caching.
    A given season's file isn't published until nflverse processes it -- when that happens,
    falls back to rebuilding the same box scores from play-by-play instead of silently
    skipping the season -- see _weekly_stats_from_pbp above. Any year where even that
    fallback fails is skipped rather than failing the whole fetch.
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
            logger.warning(f"nflverse weekly stats not available for {year} ({e}) -- falling back to play-by-play.")

        try:
            frames.append(_weekly_stats_from_pbp(year, get_schedules([year])))
        except Exception as e:
            logger.warning(f"Play-by-play fallback also failed for {year}: {e}")

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
