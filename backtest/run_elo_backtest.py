#!/usr/bin/env python3
"""
Backtest the new Elo-based NFL moneyline model against real past seasons.
Run: python -m backtest.run_elo_backtest
Run: python -m backtest.run_elo_backtest --test-years 2014-2024 --burn-in-years 1999-2013
Run: python -m backtest.run_elo_backtest --sweep         — threshold sweep
Run: python -m backtest.run_elo_backtest --no-qb          — team-only Elo, no QB adjustment
Run: python -m backtest.run_elo_backtest --compare-qb-scale  — try a few QB adjustment strengths
"""

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

SWEEP_THRESHOLDS_PROB = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
QB_SCALE_OPTIONS = [0.0, 15.0, 25.0, 40.0]


def parse_year_range(text):
    """Parse '2019-2024' or a single year like '2024' into a list of years."""
    if "-" in text:
        start, end = text.split("-")
        return list(range(int(start), int(end) + 1))
    return [int(text)]


def print_summary(label, summary):
    if summary is None:
        print(f"{label}: no bets flagged")
        return
    print(f"{label}: {summary['bets']:4d} bets  |  {summary['wins']}-{summary['losses']}  |  "
          f"win rate {summary['win_rate']*100:.1f}%  |  ROI {summary['roi_pct']:+.1f}%  |  "
          f"{summary['total_profit_units']:+.1f} units")


def main():
    parser = argparse.ArgumentParser(description="Backtest the Elo-based NFL moneyline model against past seasons")
    parser.add_argument("--test-years", default="2014-2024", help="Seasons to backtest, e.g. 2014-2024")
    parser.add_argument("--burn-in-years", default="1999-2013", help="Seasons used only to warm up Elo ratings")
    parser.add_argument("--sweep", action="store_true", help="Print a full edge-threshold sweep")
    parser.add_argument("--no-qb", action="store_true", help="Disable the QB adjustment layer (team-only Elo)")
    parser.add_argument("--compare-qb-scale", action="store_true", help="Compare a few QB adjustment strengths side by side")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    from backtest.simulate_elo import load_elo_history, grade_elo_history, calibration_summary, summarize_elo_results
    from model.nfl_elo_ratings import ELO_K, HOME_FIELD_ELO
    from model.qb_adjustment import QB_ELO_SCALE

    test_years = parse_year_range(args.test_years)
    burn_in_years = parse_year_range(args.burn_in_years)
    use_qb = not args.no_qb

    print("\n" + "=" * 60)
    print(f"ELO MONEYLINE BACKTEST — {min(test_years)}-{max(test_years)}  "
          f"(burn-in {min(burn_in_years)}-{max(burn_in_years)})")
    print(f"QB adjustment: {'ON (scale=' + str(QB_ELO_SCALE) + ')' if use_qb else 'OFF'}")
    print("=" * 60)

    history_df, _ = load_elo_history(burn_in_years, test_years, use_qb_adjustment=use_qb)
    results = grade_elo_history(history_df, test_years)
    calib = calibration_summary(history_df, test_years)

    print(f"\nAccuracy check ({calib['n']} decided games, lower Brier score = more accurate):")
    print(f"  Model Elo win-probability Brier:  {calib['model_brier']}")
    print(f"  Market (devigged) Brier:           {calib['market_brier']}")

    print(f"\nAt a range of edge thresholds (edge = model win prob vs. market's devigged win prob):")
    for t in SWEEP_THRESHOLDS_PROB:
        print_summary(f"  edge >= {t:.2f}", summarize_elo_results(results, min_edge=t))

    if args.sweep:
        print("\nFull sweep, finer steps:")
        for t in [x / 100 for x in range(0, 41, 2)]:
            print_summary(f"  edge >= {t:.2f}", summarize_elo_results(results, min_edge=t))

    if args.compare_qb_scale:
        print("\n" + "=" * 60)
        print("QB ADJUSTMENT SCALE COMPARISON")
        print("=" * 60)
        for scale in QB_SCALE_OPTIONS:
            hist, _ = load_elo_history(burn_in_years, test_years, use_qb_adjustment=(scale > 0), qb_elo_scale=scale)
            res = grade_elo_history(hist, test_years)
            cal = calibration_summary(hist, test_years)
            print(f"\nQB scale = {scale}  (Brier: model={cal['model_brier']}, market={cal['market_brier']})")
            for t in [0.0, 0.10, 0.20]:
                print_summary(f"  edge >= {t:.2f}", summarize_elo_results(res, min_edge=t))

    print()


if __name__ == "__main__":
    main()
