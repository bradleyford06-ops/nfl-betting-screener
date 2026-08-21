#!/usr/bin/env python3
"""
Backtest the FBS college football game model (spreads/totals) against real past seasons.
Run: python backtest/run_cfb_backtest.py
Run: python backtest/run_cfb_backtest.py --test-years 2019-2024 --burn-in-years 2017-2018
Run: python backtest/run_cfb_backtest.py --sweep   — also print a threshold sweep, to help pick real edge thresholds
"""

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

SWEEP_THRESHOLDS = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20]


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
    parser = argparse.ArgumentParser(description="Backtest the FBS college football game model against past seasons")
    parser.add_argument("--test-years", default="2019-2024", help="Seasons to backtest, e.g. 2019-2024")
    parser.add_argument("--burn-in-years", default="2017-2018", help="Seasons used only to warm up ratings")
    parser.add_argument("--sweep", action="store_true", help="Print a threshold sweep for spread and total separately")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    from backtest.simulate_cfb import run_cfb_backtest, summarize_results
    from model.cfb_power_ratings import SPREAD_EDGE_THRESHOLD, TOTAL_EDGE_THRESHOLD

    test_years = parse_year_range(args.test_years)
    burn_in_years = parse_year_range(args.burn_in_years)

    results = run_cfb_backtest(burn_in_years, test_years)

    print("\n" + "=" * 60)
    print(f"CFB BACKTEST RESULTS — {min(test_years)}-{max(test_years)}")
    print("Note: ROI assumes standard -110 odds on every bet — cfbd's historical lines")
    print("don't include the actual spread/total price, unlike the NFL backtest. Win rate")
    print("is exact; ROI is an estimate under standard juice.")
    print("=" * 60)

    if not results:
        print("No games were gradable across the entire backtest period.")
        return

    spread_results = [r for r in results if r["market"] == "spread"]
    total_results = [r for r in results if r["market"] == "total"]

    print(f"\nAt the current live thresholds (spread >= {SPREAD_EDGE_THRESHOLD}, total >= {TOTAL_EDGE_THRESHOLD}):")
    print_summary("  SPREAD", summarize_results(spread_results, min_edge=SPREAD_EDGE_THRESHOLD))
    print_summary("  TOTAL ", summarize_results(total_results, min_edge=TOTAL_EDGE_THRESHOLD))

    if args.sweep:
        print("\nSPREAD threshold sweep:")
        for t in SWEEP_THRESHOLDS:
            print_summary(f"  edge >= {t:4.1f}", summarize_results(spread_results, min_edge=t))

        print("\nTOTAL threshold sweep:")
        for t in SWEEP_THRESHOLDS:
            print_summary(f"  edge >= {t:4.1f}", summarize_results(total_results, min_edge=t))

    print()


if __name__ == "__main__":
    main()
