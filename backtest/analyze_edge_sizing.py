#!/usr/bin/env python3
"""
Does a bigger edge actually deserve a bigger bet?

Every backtest and every real pick in this project's ledger stakes a flat 1 unit,
regardless of how big the edge was. This script tests, for the 5 markets already
proven to have real backtested edge (NFL spread, CFB spread, NHL moneyline, NHL total,
MLB run line), whether edge size actually predicts outcome quality — not just whether
crossing one threshold works, but whether a bigger edge keeps paying off as it grows —
and whether staking proportionally to edge (via a fractional-Kelly formula, see
model/staking.py) would have beaten today's flat staking on the exact same bets.

Run: python backtest/analyze_edge_sizing.py
"""

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def build_market_specs():
    """Deferred import so --help doesn't pay the cost of loading every model module."""
    from backtest.simulate import run_backtest
    from backtest.simulate_cfb import run_cfb_backtest, STANDARD_ODDS as CFB_STANDARD_ODDS
    from backtest.simulate_nhl import run_nhl_backtest
    from backtest.simulate_mlb import run_mlb_backtest
    from model.power_ratings import SPREAD_EDGE_THRESHOLD as NFL_SPREAD_THRESHOLD, MARGIN_STD_DEV as NFL_MARGIN_STD_DEV
    from model.cfb_power_ratings import SPREAD_EDGE_THRESHOLD as CFB_SPREAD_THRESHOLD, MARGIN_STD_DEV as CFB_MARGIN_STD_DEV
    from model.nhl_power_ratings import (
        MONEYLINE_EDGE_THRESHOLD as NHL_ML_THRESHOLD,
        TOTAL_EDGE_THRESHOLD as NHL_TOTAL_THRESHOLD,
        MARGIN_STD_DEV as NHL_MARGIN_STD_DEV,
    )
    from model.mlb_power_ratings import RUNLINE_EDGE_THRESHOLD as MLB_RUNLINE_THRESHOLD

    return [
        {
            "label": "NFL Spread",
            "run_fn": run_backtest,
            "run_args": ([2017, 2018], list(range(2019, 2025))),
            "market_key": "spread",
            "edge_threshold": NFL_SPREAD_THRESHOLD,
            "bucket_edges": [0, 4, 8, 10, 12, 14, 999],
            "prob_kind": "points",
            "margin_std_dev": NFL_MARGIN_STD_DEV,
            "standard_odds": None,
        },
        {
            "label": "CFB Spread",
            "run_fn": run_cfb_backtest,
            "run_args": ([2017, 2018], list(range(2019, 2025))),
            "market_key": "spread",
            "edge_threshold": CFB_SPREAD_THRESHOLD,
            "bucket_edges": [0, 2, 4, 6, 8, 10, 12, 999],
            "prob_kind": "points",
            "margin_std_dev": CFB_MARGIN_STD_DEV,
            "standard_odds": CFB_STANDARD_ODDS,
        },
        {
            "label": "NHL Moneyline",
            "run_fn": run_nhl_backtest,
            "run_args": ([2019], [2021, 2022]),
            "market_key": "moneyline",
            "edge_threshold": NHL_ML_THRESHOLD,
            "bucket_edges": [0, 0.05, 0.10, 0.15, 0.20, 0.25, 999],
            "prob_kind": "stored",
            "margin_std_dev": None,
            "standard_odds": None,
        },
        {
            "label": "NHL Total",
            "run_fn": run_nhl_backtest,
            "run_args": ([2019], [2021, 2022]),
            "market_key": "total",
            "edge_threshold": NHL_TOTAL_THRESHOLD,
            "bucket_edges": [0, 0.5, 1.0, 1.5, 2.0, 999],
            "prob_kind": "points",
            "margin_std_dev": NHL_MARGIN_STD_DEV,
            "standard_odds": None,
        },
        {
            "label": "MLB Run Line",
            "run_fn": run_mlb_backtest,
            "run_args": ([2016, 2017], list(range(2018, 2022))),
            "market_key": "runline",
            "edge_threshold": MLB_RUNLINE_THRESHOLD,
            "bucket_edges": [0, 0.05, 0.10, 0.15, 0.20, 0.30, 999],
            "prob_kind": "stored",
            "margin_std_dev": None,
            "standard_odds": None,
        },
    ]


def bucket_report(decided, bucket_edges):
    """Disjoint edge-size buckets (not a cumulative sweep) — win rate and flat-stake ROI
    per bucket, so we can see whether performance actually climbs bucket-over-bucket or
    is flat/noisy above the live threshold."""
    import pandas as pd

    df = pd.DataFrame(decided)
    df["bucket"] = pd.cut(df["edge_score"], bins=bucket_edges, right=False)
    rows = []
    for bucket, group in df.groupby("bucket", observed=True):
        n = len(group)
        if n == 0:
            continue
        wins = (group["outcome"] == "win").sum()
        rows.append({
            "bucket": str(bucket),
            "n": n,
            "win_rate": wins / n,
            "roi_pct": group["profit_units"].sum() / n * 100,
        })
    return rows


def edge_outcome_correlation(decided):
    """Simple correlation between edge_score and win (1) / loss (0) across all decided
    bets — a sanity check that any bucket-to-bucket climb isn't just one lucky bucket."""
    import pandas as pd

    df = pd.DataFrame(decided)
    df["won"] = (df["outcome"] == "win").astype(int)
    return df["edge_score"].corr(df["won"])


def scaled_vs_flat_roi(qualifying, spec):
    """
    Restricted to bets that clear today's live threshold (the ones that would actually
    be flagged in production) — compare flat 1-unit staking (today's real behavior)
    against a fractional-Kelly stake sized to each bet's own edge, on the exact same
    set of bets. This is the real test of 'would sizing up on bigger edges have helped.'
    """
    from model.staking import kelly_fraction, suggested_stake_units, model_prob_from_points_edge

    if not qualifying:
        return None

    def bet_model_prob(r):
        if spec["prob_kind"] == "stored":
            return r["model_prob"]
        return model_prob_from_points_edge(r["edge_score"], spec["margin_std_dev"])

    def bet_odds(r):
        return spec["standard_odds"] if spec["standard_odds"] is not None else r["odds"]

    # Normalize "1 unit" to a bet sitting exactly at the live threshold, priced at this
    # market's own median odds — the anchor every other bet's stake size is relative to.
    import statistics
    median_odds = statistics.median(bet_odds(r) for r in qualifying)
    if spec["prob_kind"] == "stored":
        threshold_prob = 0.5 + spec["edge_threshold"]  # a fair-market bet with exactly the threshold's edge
    else:
        threshold_prob = model_prob_from_points_edge(spec["edge_threshold"], spec["margin_std_dev"])
    threshold_kelly = kelly_fraction(threshold_prob, median_odds) * 0.25

    flat_profit, flat_n = 0.0, 0
    scaled_profit, scaled_staked = 0.0, 0.0
    stakes = []
    for r in qualifying:
        if r["outcome"] == "push":
            continue
        flat_n += 1
        flat_profit += r["profit_units"]

        stake = suggested_stake_units(bet_model_prob(r), bet_odds(r), threshold_kelly, kelly_multiplier=0.25)
        stakes.append(stake)
        scaled_staked += stake
        scaled_profit += stake * r["profit_units"]

    if flat_n == 0:
        return None

    return {
        "n": flat_n,
        "flat_roi_pct": flat_profit / flat_n * 100,
        "flat_profit_units": flat_profit,
        "scaled_roi_pct": scaled_profit / scaled_staked * 100 if scaled_staked else None,
        "scaled_profit_units": scaled_profit,
        "scaled_total_staked": scaled_staked,
        "avg_stake": sum(stakes) / len(stakes) if stakes else None,
        "max_stake": max(stakes) if stakes else None,
    }


def main():
    parser = argparse.ArgumentParser(description="Test whether edge size predicts profitability, and whether Kelly-scaled staking beats flat staking")
    args = parser.parse_args()  # noqa: F841 — reserved for future --market/--years overrides

    from dotenv import load_dotenv
    load_dotenv()

    specs = build_market_specs()
    cache = {}  # (run_fn, run_args) -> raw results, since NHL/MLB backtests cover 2+ markets per run

    print("\n" + "=" * 72)
    print("EDGE-SIZING ANALYSIS — does a bigger edge deserve a bigger bet?")
    print("=" * 72)

    for spec in specs:
        cache_key = (spec["run_fn"], tuple(spec["run_args"][0]), tuple(spec["run_args"][1]))
        if cache_key not in cache:
            logger.info(f"Running backtest for {spec['label']}...")
            cache[cache_key] = spec["run_fn"](*spec["run_args"])
        results = cache[cache_key]

        market_results = [r for r in results if r["market"] == spec["market_key"]]
        decided = [r for r in market_results if r["outcome"] != "push"]

        print(f"\n--- {spec['label']} " + "-" * (60 - len(spec["label"])))
        if not decided:
            print("  No graded bets found.")
            continue

        print(f"  {len(decided)} decided bets total (all edge sizes, threshold-free)")

        print("\n  Edge-size buckets (disjoint, not cumulative):")
        for row in bucket_report(decided, spec["bucket_edges"]):
            print(f"    {row['bucket']:>16s}  n={row['n']:4d}  win rate {row['win_rate']*100:5.1f}%  ROI {row['roi_pct']:+6.1f}%")

        corr = edge_outcome_correlation(decided)
        print(f"\n  Correlation between edge_score and winning: {corr:+.3f}")

        qualifying = [r for r in decided if r["edge_score"] >= spec["edge_threshold"]]
        comparison = scaled_vs_flat_roi(qualifying, spec)
        print(f"\n  At the live threshold (edge >= {spec['edge_threshold']}), flat vs. Kelly-scaled staking:")
        if comparison is None:
            print("    Not enough qualifying bets to compare.")
        else:
            print(f"    Flat 1-unit:     {comparison['n']} bets  |  ROI {comparison['flat_roi_pct']:+.1f}%  |  {comparison['flat_profit_units']:+.1f} units profit")
            if comparison["scaled_roi_pct"] is not None:
                print(f"    Kelly-scaled:    {comparison['n']} bets  |  ROI {comparison['scaled_roi_pct']:+.1f}%  |  "
                      f"{comparison['scaled_profit_units']:+.1f} units profit on {comparison['scaled_total_staked']:.1f} units staked  |  "
                      f"avg stake {comparison['avg_stake']:.2f}u, max {comparison['max_stake']:.2f}u")
            else:
                print("    Kelly-scaled:    no capital staked (every bet's Kelly fraction came back at or below zero)")

    print("\n" + "=" * 72)
    print("Read this before wiring anything into the live dashboard:")
    print("  - A bucket table that climbs steadily = real signal worth sizing on.")
    print("  - A flat or noisy bucket table (even if the live threshold itself backtests")
    print("    well) means the edge number distinguishes 'bet or don't' but not 'bet more'")
    print("    — sizing on it would be adding false precision, not real information.")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    main()
