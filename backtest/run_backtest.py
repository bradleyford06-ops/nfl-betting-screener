#!/usr/bin/env python3
"""
Backtest the game model (spreads/totals/moneylines) against real past seasons.
Run: python backtest/run_backtest.py
Run: python backtest/run_backtest.py --test-years 2019-2024 --burn-in-years 2017-2018
Run: python backtest/run_backtest.py --sweep   — also print a threshold sweep, to help pick real edge thresholds
"""

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

SWEEP_THRESHOLDS_POINTS = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20]
SWEEP_THRESHOLDS_PROB = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]


def parse_year_range(text):
    """Parse '2019-2024' or a single year like '2024' into a list of years."""
    if "-" in text:
        start, end = text.split("-")
        return list(range(int(start), int(end) + 1))
    return [int(text)]


def print_summary(label, summary):
    if summary["overall"] is None:
        print(f"{label}: no bets flagged")
        return
    o = summary["overall"]
    print(f"{label}: {o['bets']:4d} bets  |  {o['wins']}-{o['losses']}  |  "
          f"win rate {o['win_rate']*100:.1f}%  |  ROI {o['roi_pct']:+.1f}%  |  "
          f"{o['total_profit_units']:+.1f} units")


def main():
    parser = argparse.ArgumentParser(description="Backtest the NFL game model against past seasons")
    parser.add_argument("--test-years", default="2019-2024", help="Seasons to backtest, e.g. 2019-2024")
    parser.add_argument("--burn-in-years", default="2017-2018", help="Seasons used only to warm up ratings")
    parser.add_argument("--sweep", action="store_true", help="Print a threshold sweep for spread/total/moneyline separately")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    from backtest.simulate import run_backtest, summarize_results
    from model.power_ratings import (
        SPREAD_EDGE_THRESHOLD, TOTAL_EDGE_THRESHOLD, TOTAL_SPREAD_CONVICTION_THRESHOLD, MONEYLINE_EDGE_THRESHOLD,
    )

    test_years = parse_year_range(args.test_years)
    burn_in_years = parse_year_range(args.burn_in_years)

    # run_backtest grades every game threshold-free; filtering by the live threshold
    # happens here, same pattern as the CFB/NHL/MLB backtests, so this one run also
    # supports a full threshold sweep or per-edge-bucket analysis without re-simulating.
    results = run_backtest(burn_in_years, test_years)

    print("\n" + "=" * 60)
    print(f"BACKTEST RESULTS — {min(test_years)}-{max(test_years)}")
    print("=" * 60)

    if not results:
        print("No games were gradable across the entire backtest period.")
        return

    spread_results = [r for r in results if r["market"] == "spread"]
    total_results = [r for r in results if r["market"] == "total"]
    moneyline_results = [r for r in results if r["market"] == "moneyline"]

    print(f"\nAt the current live thresholds (spread >= {SPREAD_EDGE_THRESHOLD}, "
          f"total >= {TOTAL_EDGE_THRESHOLD} AND spread >= {TOTAL_SPREAD_CONVICTION_THRESHOLD} "
          f"in the same game, moneyline >= {MONEYLINE_EDGE_THRESHOLD}):")
    print_summary("  SPREAD    ", summarize_results(spread_results, min_edge=SPREAD_EDGE_THRESHOLD))
    print_summary("  TOTAL     ", summarize_results(total_results, min_edge=TOTAL_EDGE_THRESHOLD, min_spread_edge=TOTAL_SPREAD_CONVICTION_THRESHOLD))
    print_summary("  MONEYLINE ", summarize_results(moneyline_results, min_edge=MONEYLINE_EDGE_THRESHOLD))

    total_flagged = [
        r for r in total_results
        if r["edge_score"] >= TOTAL_EDGE_THRESHOLD and (r.get("spread_edge") or 0) >= TOTAL_SPREAD_CONVICTION_THRESHOLD
    ]
    overall_at_threshold = summarize_results(
        [r for r in spread_results if r["edge_score"] >= SPREAD_EDGE_THRESHOLD]
        + total_flagged
        + [r for r in moneyline_results if r["edge_score"] >= MONEYLINE_EDGE_THRESHOLD]
    )
    if overall_at_threshold["overall"] and overall_at_threshold["overall"]["bets"] < 100:
        print(f"\nNote: only {overall_at_threshold['overall']['bets']} bets total at these thresholds — "
              f"too small a sample to draw firm conclusions from.")

    if args.sweep:
        print("\nSPREAD threshold sweep (points):")
        for t in SWEEP_THRESHOLDS_POINTS:
            print_summary(f"  edge >= {t:4.1f}", summarize_results(spread_results, min_edge=t))

        print(f"\nTOTAL threshold sweep (points) — spread-conviction co-filter fixed at >= {TOTAL_SPREAD_CONVICTION_THRESHOLD} "
              f"(this is the live rule; see model/power_ratings.py's total_conviction_ok):")
        for t in SWEEP_THRESHOLDS_POINTS:
            print_summary(f"  edge >= {t:4.1f}", summarize_results(total_results, min_edge=t, min_spread_edge=TOTAL_SPREAD_CONVICTION_THRESHOLD))

        print("\nTOTAL threshold sweep (points) — WITHOUT the spread-conviction co-filter, for comparison:")
        for t in SWEEP_THRESHOLDS_POINTS:
            print_summary(f"  edge >= {t:4.1f}", summarize_results(total_results, min_edge=t))

        print("\nMONEYLINE threshold sweep (probability):")
        for t in SWEEP_THRESHOLDS_PROB:
            print_summary(f"  edge >= {t:4.2f}", summarize_results(moneyline_results, min_edge=t))

    print()


if __name__ == "__main__":
    main()
