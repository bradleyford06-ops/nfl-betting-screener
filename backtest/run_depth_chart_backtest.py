#!/usr/bin/env python3
"""
Tests whether limiting player props to only "top of depth chart" players (RB1, WR1/WR2,
TE1 — approximated by trailing usage share, since no reliable real depth-chart source
exists; see model/usage_rank.py) changes hit rate or bet volume, for both the trend model
and the coverage model. Compares each model's live threshold with vs. without the depth
chart filter, on the same synthetic-line backtest methodology used everywhere else in this
project — see run_props_backtest.py for the caveat that implies.

Run: python backtest/run_depth_chart_backtest.py
"""

import logging
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

TOP_N_PER_WEEK_OPTIONS = [10, 15, 20]  # alternative to the depth-chart filter for coverage volume

YEARS = [2019, 2020, 2021, 2022, 2023, 2024]
WEEKS_PER_SEASON = 18  # regular season only; matches what's actually screened live

# Each combo's already-calibrated live threshold(s) — see POSITION_STAT_THRESHOLDS in
# model/player_trends.py and COVERAGE_EDGE_THRESHOLD in model/coverage_sim.py.
TREND_COMBOS = [
    # (position, stat_column, player_threshold, defense_threshold) — matches
    # POSITION_STAT_THRESHOLDS in model/player_trends.py exactly.
    ("WR", "receiving_yards", 0.20, 0.08),
    ("RB", "receiving_yards", 0.20, 0.08),
    ("TE", "receiving_yards", 0.25, 0.03),
    ("RB", "rushing_yards", 0.12, 0.08),
]
COVERAGE_COMBOS = [("WR", "receptions"), ("WR", "receiving_yards"), ("RB", "receptions"),
                    ("RB", "receiving_yards"), ("TE", "receptions"), ("TE", "receiving_yards")]


def summarize(label, flagged_df):
    if flagged_df.empty:
        print(f"  {label:32s} n=0")
        return None
    predicted_over = flagged_df["edge_col"] > 0
    correct = (predicted_over & flagged_df["actual_beat_line"]) | (~predicted_over & ~flagged_df["actual_beat_line"])
    n = len(flagged_df)
    weeks = flagged_df[["season", "week"]].drop_duplicates()
    per_week = n / max(len(weeks), 1)
    print(f"  {label:32s} n={n:4d}  hit rate={correct.mean()*100:5.1f}%  avg picks/week={per_week:.2f}")
    return {"n": n, "hit_rate": round(correct.mean(), 3), "per_week": round(per_week, 2)}


def main():
    from dotenv import load_dotenv
    load_dotenv()

    from screener.fetch_stats import get_weekly_player_stats
    from screener.fetch_pbp import get_play_by_play
    from model.usage_rank import add_usage_rank, DEPTH_CHART_LIMITS
    from backtest.simulate_props import compute_synthetic_bets
    from backtest.simulate_coverage_v2 import compute_synthetic_simplified_bets

    logger.info("Loading weekly player stats and play-by-play (this is the slow part)...")
    weekly_df = get_weekly_player_stats(YEARS)
    weekly_df = add_usage_rank(weekly_df)
    pbp_df = get_play_by_play(YEARS)

    print("\n" + "=" * 78)
    print(f"DEPTH-CHART FILTER BACKTEST — {YEARS[0]}-{YEARS[-1]} (limits: {DEPTH_CHART_LIMITS})")
    print("=" * 78)

    print("\n--- TREND MODEL (at each combo's live threshold) ---")
    for position, stat_column, player_threshold, defense_threshold in TREND_COMBOS:
        limit = DEPTH_CHART_LIMITS[position]
        logger.info(f"Computing {position} {stat_column}...")
        results = compute_synthetic_bets(weekly_df, position, stat_column)
        if results.empty:
            print(f"\n{position} {stat_column}: no data")
            continue

        same_direction = (
            ((results["player_edge_pct"] > 0) & (results["defense_edge_pct"] > 0))
            | ((results["player_edge_pct"] < 0) & (results["defense_edge_pct"] < 0))
        )
        passes = (
            same_direction
            & (results["player_edge_pct"].abs() >= player_threshold)
            & (results["defense_edge_pct"].abs() >= defense_threshold)
        )
        flagged = results[passes].copy()
        flagged["edge_col"] = flagged["player_edge_pct"]

        print(f"\n{position} {stat_column} (player>={player_threshold}, defense>={defense_threshold}, "
              f"depth-chart limit=top {limit}):")
        summarize("All players (current)", flagged)
        summarize(f"Top-{limit} at position only", flagged[flagged["usage_rank"] <= limit])

    print("\n--- COVERAGE MODEL (at live threshold 0.20) ---")
    all_coverage_flagged = []
    all_coverage_results = []
    for position, stat_column in COVERAGE_COMBOS:
        limit = DEPTH_CHART_LIMITS[position]
        logger.info(f"Computing coverage {position} {stat_column}...")
        results = compute_synthetic_simplified_bets(weekly_df, pbp_df, position, stat_column)
        if results.empty:
            print(f"\n{position} {stat_column}: no data")
            continue
        results = results.copy()
        results["combo"] = f"{position} {stat_column}"
        all_coverage_results.append(results)

        flagged = results[results["edge_pct"].abs() >= 0.20].copy()
        flagged["edge_col"] = flagged["edge_pct"]
        all_coverage_flagged.append(flagged)

        print(f"\n{position} {stat_column} (depth-chart limit=top {limit}):")
        summarize("All players (current)", flagged)
        summarize(f"Top-{limit} at position only", flagged[flagged["usage_rank"] <= limit])

    print("\n--- COVERAGE MODEL: TOP-N-PER-WEEK CAP (alternative to depth-chart filter) ---")
    print("(All six combos pooled together, since that's how they'd compete for a weekly cap in practice)")
    combined = pd.concat(all_coverage_flagged, ignore_index=True)
    summarize("All coverage picks (current)", combined)
    for n in TOP_N_PER_WEEK_OPTIONS:
        capped = (
            combined.reindex(combined["edge_col"].abs().sort_values(ascending=False).index)
            .groupby(["season", "week"], group_keys=False)
            .head(n)
        )
        summarize(f"Top-{n} per week by edge size", capped)

    print("\n--- COVERAGE MODEL: RAISING THE UNIFORM EDGE THRESHOLD (alternative lever) ---")
    print("(All six combos pooled; unlike the top-N cap above, this raises the bar for every pick,")
    print(" not just the biggest edges within a week — matches the pattern already proven in CLAUDE.md)")
    all_results = pd.concat(all_coverage_results, ignore_index=True)
    for threshold in [0.20, 0.25, 0.30, 0.35, 0.40]:
        at_threshold = all_results[all_results["edge_pct"].abs() >= threshold].copy()
        at_threshold["edge_col"] = at_threshold["edge_pct"]
        summarize(f"Threshold >= {threshold:.2f}", at_threshold)

    print()


if __name__ == "__main__":
    main()
