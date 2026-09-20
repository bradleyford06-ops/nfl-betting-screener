import pandas as pd

USAGE_RANK_GAMES_WINDOW = 8  # matches the trend model's own recent-form window

# How many players per team/position count as "top of the depth chart" for this filter.
# WR gets 2 (WR1 and WR2 both start in most base formations); RB and TE get 1.
DEPTH_CHART_LIMITS = {"RB": 1, "WR": 2, "TE": 1}


def add_usage_rank(weekly_df, window=USAGE_RANK_GAMES_WINDOW):
    """
    Adds a `usage_rank` column: each player's rank (1 = most-used) among their own team's
    players at the same position, for that season/week, based on trailing usage over their
    prior `window` games. There's no reliable real depth-chart data source available (see
    CLAUDE.md) so this approximates "starter" using actual on-field usage instead — targets
    for WR/TE, carries+targets for RB — which self-corrects for injuries/rotations faster
    than an official depth chart would anyway.

    Trailing usage only looks at games strictly before the row's own week (via shift(1)),
    so this carries no lookahead bias when used in a walk-forward backtest.
    """
    df = weekly_df.copy()
    is_rb = df["position"] == "RB"
    df["usage_metric"] = df["targets"].fillna(0) + (is_rb * df["carries"].fillna(0))

    df = df.sort_values(["player_id", "season", "week"])
    df["trailing_usage"] = (
        df.groupby("player_id")["usage_metric"]
        .transform(lambda s: s.shift(1).rolling(window, min_periods=1).mean())
    )

    df["usage_rank"] = (
        df.groupby(["recent_team", "position", "season", "week"])["trailing_usage"]
        .rank(ascending=False, method="first")
    )

    return df.drop(columns=["usage_metric", "trailing_usage"])


def current_usage_rank_lookup(weekly_df, window=USAGE_RANK_GAMES_WINDOW):
    """
    Live-screening counterpart to add_usage_rank: one usage_rank per player as of right now
    (using their most recent `window` games, not walked forward week by week), keyed by
    player_display_name for O(1) lookup during screening. Used to gate the trend model's
    props to top-of-depth-chart players only — see CLAUDE.md's 2026-09-19 depth-chart
    investigation for why this filter is applied to the trend model but not the coverage
    model.
    """
    df = weekly_df.sort_values(["player_id", "season", "week"]).copy()
    is_rb = df["position"] == "RB"
    df["usage_metric"] = df["targets"].fillna(0) + (is_rb * df["carries"].fillna(0))

    latest = df.groupby("player_id").tail(1).set_index("player_id")[["player_display_name", "position", "recent_team"]]
    latest["trailing_usage"] = df.groupby("player_id")["usage_metric"].apply(lambda s: s.tail(window).mean())
    latest["usage_rank"] = latest.groupby(["recent_team", "position"])["trailing_usage"].rank(ascending=False, method="first")

    return latest.set_index("player_display_name")["usage_rank"].to_dict()


def within_depth_chart_limit(position, usage_rank):
    """True if usage_rank qualifies as 'top of depth chart' for this position, per
    DEPTH_CHART_LIMITS. Positions with no configured limit (QB, FB) always pass."""
    limit = DEPTH_CHART_LIMITS.get(position)
    if limit is None or pd.isna(usage_rank):
        return True
    return usage_rank <= limit
