"""
Reusable performance-trend analysis for graded player-prop picks. Built during the
2026-09-15 Week 1 review (see .claude/memory/sessions/2026-09-15-stats-fallback-and-first-
analysis.md for that review's findings) so the same breakdowns can be re-run as more weeks
of data accumulate, instead of re-deriving them from scratch each time.

Not part of the live screener -- this reads the ledger after the fact. Typical use:

    from analysis.props_performance import load_graded_props, attach_position, win_rate_table

    df = load_graded_props(season=2026)
    df = attach_position(df, stats_years=[2025, 2026])
    print(win_rate_table(df, ["position"]))
"""
import math
import sqlite3

import pandas as pd

from screener.ledger import DB_PATH
from screener.fetch_stats import get_weekly_player_stats
from model.player_trends import resolve_player_display_name


def load_graded_props(season=None, week=None, strategies=("props_trend", "props_coverage", "props_speculative")):
    """Every graded (won/lost) player-prop pick, optionally filtered to one season/week."""
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" for _ in strategies)
    query = f"SELECT * FROM picks WHERE strategy IN ({placeholders}) AND status IN ('won','lost')"
    params = list(strategies)
    if season is not None:
        query += " AND season = ?"
        params.append(season)
    if week is not None:
        query += " AND week = ?"
        params.append(week)
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    df["won"] = (df["status"] == "won").astype(int)
    return df


def attach_position(df, stats_years):
    """Add each pick's player position by resolving `subject` (however it was spelled at
    pick time -- the odds provider's own spelling, for picks made before the name-matching
    fix) against real weekly stats, the same way the live screener does."""
    weekly_df = get_weekly_player_stats(stats_years)

    def get_position(name):
        resolved = resolve_player_display_name(weekly_df, name)
        if resolved is None:
            return None
        rows = weekly_df[weekly_df["player_display_name"] == resolved]
        return rows["position"].iloc[-1] if len(rows) else None

    df = df.copy()
    df["position"] = df["subject"].apply(get_position)
    return df


def win_rate_table(df, group_cols):
    """n / wins / win_rate, grouped by one or more columns -- the basic building block
    every breakdown in the Week 1 review (strategy, market, position, side, edge bucket)
    was built from."""
    return df.groupby(list(group_cols)).agg(n=("won", "count"), wins=("won", "sum"), win_rate=("won", "mean"))


def edge_score_buckets(df, n_buckets=4):
    """Split into equal-sized edge_score buckets -- the "does a bigger disagreement mean a
    more reliable pick" check."""
    df = df.copy()
    df["edge_bucket"] = pd.qcut(df["edge_score"], n_buckets, duplicates="drop")
    return win_rate_table(df, ["edge_bucket"])


def line_vs_actual_gap(df):
    """How far the real result landed from the flagged line, signed so positive always
    means "the number came in above the line" -- averaged by side, this is what showed
    Week 1's WR overs missing high by ~5.9 units on average."""
    df = df.copy()
    df["line"] = df["line"].astype(float)
    df["actual_value"] = df["actual_value"].astype(float)
    df["gap"] = df["actual_value"] - df["line"]
    return df


def two_proportion_significance(wins_a, n_a, wins_b, n_b):
    """Two-sided z-test for whether two win rates are really different or just noise --
    used throughout the Week 1 review (e.g. WR vs. every other position, p=0.0002).
    Returns (z, p_value)."""
    p_a, p_b = wins_a / n_a, wins_b / n_b
    p_pool = (wins_a + wins_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    if se == 0:
        return 0.0, 1.0
    z = (p_a - p_b) / se
    p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return z, p_value
