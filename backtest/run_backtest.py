#!/usr/bin/env python3
"""
Backtest the game model (spreads/totals/moneylines) against real past seasons.
Run: python backtest/run_backtest.py
Run: python backtest/run_backtest.py --test-years 2019-2024 --burn-in-years 2017-2018
"""

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def parse_year_range(text):
    """Parse '2019-2024' or a single year like '2024' into a list of years."""
    if "-" in text:
        start, end = text.split("-")
        return list(range(int(start), int(end) + 1))
    return [int(text)]


def main():
    parser = argparse.ArgumentParser(description="Backtest the NFL game model against past seasons")
    parser.add_argument("--test-years", default="2019-2024", help="Seasons to backtest, e.g. 2019-2024")
    parser.add_argument("--burn-in-years", default="2017-2018", help="Seasons used only to warm up ratings")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    from backtest.simulate import run_backtest, summarize_results

    test_years = parse_year_range(args.test_years)
    burn_in_years = parse_year_range(args.burn_in_years)

    results = run_backtest(burn_in_years, test_years)
    summary = summarize_results(results)

    print("\n" + "=" * 60)
    print(f"BACKTEST RESULTS — {min(test_years)}-{max(test_years)}")
    print("=" * 60)

    if summary["overall"] is None:
        print("No bets were flagged across the entire backtest period.")
        return

    o = summary["overall"]
    print(f"\nOVERALL: {o['bets']} bets  |  {o['wins']}-{o['losses']}  |  "
          f"win rate {o['win_rate']*100:.1f}%  |  ROI {o['roi_pct']:+.1f}%  |  "
          f"{o['total_profit_units']:+.1f} units")

    print("\nBY MARKET:")
    for market, m in summary["by_market"].items():
        print(f"  {market.upper():10s}  {m['bets']:4d} bets  |  {m['wins']}-{m['losses']}  |  "
              f"win rate {m['win_rate']*100:.1f}%  |  ROI {m['roi_pct']:+.1f}%  |  "
              f"{m['total_profit_units']:+.1f} units")

    if o["bets"] < 100:
        print(f"\nNote: only {o['bets']} bets total — too small a sample to draw firm conclusions from.")

    print()


if __name__ == "__main__":
    main()
