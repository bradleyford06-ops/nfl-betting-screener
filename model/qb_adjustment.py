import numpy as np
import pandas as pd

from model.nfl_elo_ratings import canonical_team

QB_TRAILING_GAMES = 16  # how many of a QB's own recent starts feed their trailing efficiency rating
TEAM_QB_BASELINE_GAMES = 32  # how many recent team-games set "what this team normally gets" at QB
QB_ELO_SCALE = 25.0  # converts one EPA/dropback of value-over-average into Elo points — provisional, calibrated via backtest sweep


def build_starter_efficiency(schedules_df, weekly_df):
    """
    For every game, look up the *designated* starting QB (schedules_df already carries this
    as home_qb_id/away_qb_id straight from official game data — no need to guess from attempt
    counts) and that QB's efficiency in the game: EPA (expected points added) per dropback,
    a single-number measure of how well they played that accounts for down/distance/situation,
    not just raw yardage. Returns one row per team per game.
    """
    qb_stats = weekly_df[weekly_df["position"] == "QB"].copy()
    qb_stats["dropbacks"] = qb_stats["attempts"] + qb_stats["sacks"].fillna(0)
    dropbacks_or_nan = np.where(qb_stats["dropbacks"] == 0, np.nan, qb_stats["dropbacks"])
    qb_stats["epa_per_dropback"] = qb_stats["passing_epa"] / dropbacks_or_nan
    # nflverse's weekly feed has one known duplicated row (Stafford, 2010 week 8) — drop
    # any repeat rather than letting it break the lookup below.
    qb_stats = qb_stats.drop_duplicates(subset=["player_id", "season", "week"])
    qb_lookup = qb_stats.set_index(["player_id", "season", "week"])["epa_per_dropback"]

    completed = schedules_df.dropna(subset=["home_score", "away_score", "home_qb_id", "away_qb_id"]).copy()

    home_rows = completed[["season", "week", "home_team", "home_qb_id"]].rename(
        columns={"home_team": "team", "home_qb_id": "qb_id"}
    )
    away_rows = completed[["season", "week", "away_team", "away_qb_id"]].rename(
        columns={"away_team": "team", "away_qb_id": "qb_id"}
    )
    starters = pd.concat([home_rows, away_rows], ignore_index=True)
    starters["team"] = starters["team"].apply(canonical_team)

    keys = list(zip(starters["qb_id"], starters["season"], starters["week"]))
    starters["epa_per_dropback"] = qb_lookup.reindex(keys).values
    return starters.dropna(subset=["epa_per_dropback"]).sort_values(["team", "season", "week"])


def compute_qb_trailing_value(starters_df, trailing_games=QB_TRAILING_GAMES):
    """
    Turn each game's raw EPA/dropback into a QB's trailing "value over league-average" rating —
    the average of their own last N starts (not including the current game, to avoid lookahead),
    relative to that season's league-average QB efficiency (since scoring environments shift
    year to year). A QB with no prior starts yet defaults to 0 (assumed average) rather than
    an artificially confident number from a single game.
    """
    df = starters_df.sort_values(["qb_id", "season", "week"]).copy()
    league_avg_by_season = df.groupby("season")["epa_per_dropback"].transform("mean")
    df["value_over_league_avg"] = df["epa_per_dropback"] - league_avg_by_season

    df["trailing_value"] = (
        df.groupby("qb_id", group_keys=False)["value_over_league_avg"]
        .apply(lambda s: s.shift(1).rolling(trailing_games, min_periods=1).mean())
        .reset_index(level=0, drop=True)
    )
    df["trailing_value"] = df["trailing_value"].fillna(0.0)
    return df


def compute_team_qb_adjustments(starters_df, team_baseline_games=TEAM_QB_BASELINE_GAMES, elo_scale=QB_ELO_SCALE):
    """
    For each team-game, compare that week's starter's trailing value (see above) against what
    the team itself has recently gotten at QB (a trailing average of its own last N starters'
    values) — isolating a *change* from the team's normal QB situation, which is exactly the
    backup-QB-drop-off (or upgrade) signal a scoring-margin-only model can't see. A team's
    long-run average QB quality is already baked into its persistent Elo rating from real
    results, so this deliberately measures the deviation from that norm, not raw QB quality,
    to avoid double-counting. Returns {(canonical_team, season, week): elo_point_adjustment}.
    """
    df = starters_df.sort_values(["team", "season", "week"]).copy()
    df["team_norm_qb_value"] = (
        df.groupby("team", group_keys=False)["trailing_value"]
        .apply(lambda s: s.shift(1).rolling(team_baseline_games, min_periods=1).mean())
        .reset_index(level=0, drop=True)
    )
    df["team_norm_qb_value"] = df["team_norm_qb_value"].fillna(0.0)
    df["qb_adjustment_elo"] = elo_scale * (df["trailing_value"] - df["team_norm_qb_value"])

    return {
        (row.team, row.season, row.week): row.qb_adjustment_elo
        for row in df.itertuples()
    }


def build_qb_adjustments(schedules_df, weekly_df, trailing_games=QB_TRAILING_GAMES,
                          team_baseline_games=TEAM_QB_BASELINE_GAMES, elo_scale=QB_ELO_SCALE):
    """One-call convenience wrapper: schedules + weekly stats in, ready-to-use QB Elo adjustment lookup out."""
    starters = build_starter_efficiency(schedules_df, weekly_df)
    starters = compute_qb_trailing_value(starters, trailing_games)
    return compute_team_qb_adjustments(starters, team_baseline_games, elo_scale)
