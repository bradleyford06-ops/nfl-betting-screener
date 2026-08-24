#!/usr/bin/env python3
"""
Backtest the MLB game model (moneyline/run-line/total) against real past seasons.
Run: python backtest/run_mlb_backtest.py
Run: python backtest/run_mlb_backtest.py --sweep   — also print a threshold sweep, to help pick real edge thresholds

Only 2012-2021 is usable as test years -- that's the coverage of the free historical
odds data. Earlier/later seasons can't be used at all (no burn-in needed before 2012
since MLB's own API covers schedule data well before then, but the odds are the
limiting factor).
"""

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

PROB_SWEEP_THRESHOLDS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]  # moneyline/run-line — probability-edge units
TOTAL_SWEEP_THRESHOLDS = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]  # total — run units


def parse_year_range(text):
    """Parse '2018-2021' or a single year like '2021' into a list of years."""
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
    parser = argparse.ArgumentParser(description="Backtest the MLB game model against past seasons")
    parser.add_argument("--test-years", default="2018-2021", help="Seasons to backtest, e.g. 2018-2021")
    parser.add_argument("--burn-in-years", default="2016-2017", help="Seasons used only to warm up ratings")
    parser.add_argument("--sweep", action="store_true", help="Print a threshold sweep for each market separately")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    from backtest.simulate_mlb import run_mlb_backtest, summarize_results
    from model.mlb_power_ratings import MONEYLINE_EDGE_THRESHOLD, RUNLINE_EDGE_THRESHOLD, TOTAL_EDGE_THRESHOLD

    test_years = parse_year_range(args.test_years)
    burn_in_years = parse_year_range(args.burn_in_years)

    results = run_mlb_backtest(burn_in_years, test_years)

    print("\n" + "=" * 60)
    print(f"MLB BACKTEST RESULTS — seasons {sorted(test_years)}")
    print("Note: moneyline and total use real historical prices. Run-line odds are ~79%")
    print("real / ~21% assumed -110 where the free data has no price. Win rate is exact")
    print("either way; ROI on the assumed-price portion is an estimate.")
    print("=" * 60)

    if not results:
        print("No games were gradable across the entire backtest period.")
        return

    by_market = {m: [r for r in results if r["market"] == m] for m in ("moneyline", "runline", "total")}

    print(f"\nAt the current live thresholds (moneyline >= {MONEYLINE_EDGE_THRESHOLD}, "
          f"runline >= {RUNLINE_EDGE_THRESHOLD}, total >= {TOTAL_EDGE_THRESHOLD}):")
    print_summary("  MONEYLINE", summarize_results(by_market["moneyline"], min_edge=MONEYLINE_EDGE_THRESHOLD))
    print_summary("  RUNLINE  ", summarize_results(by_market["runline"], min_edge=RUNLINE_EDGE_THRESHOLD))
    print_summary("  TOTAL    ", summarize_results(by_market["total"], min_edge=TOTAL_EDGE_THRESHOLD))

    if args.sweep:
        for market, thresholds in (("moneyline", PROB_SWEEP_THRESHOLDS), ("runline", PROB_SWEEP_THRESHOLDS), ("total", TOTAL_SWEEP_THRESHOLDS)):
            print(f"\n{market.upper()} threshold sweep:")
            for t in thresholds:
                print_summary(f"  edge >= {t:5.2f}", summarize_results(by_market[market], min_edge=t))

    print()


if __name__ == "__main__":
    main()
