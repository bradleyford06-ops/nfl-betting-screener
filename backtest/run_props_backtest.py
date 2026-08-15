#!/usr/bin/env python3
"""
Backtest the player props signal against each player's own trailing average, since no
free source of historical sportsbook prop lines exists. See CLAUDE.md for the caveat this
implies: a positive result here shows the signal has real predictive content, not that it
beats an actual market line (books already price in most of what a simple trend captures).

Run: python backtest/run_props_backtest.py
"""

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

# The position/stat combos worth comparing against each other — same stat, different
# position, to see whether volatility (and therefore the right threshold) really differs.
COMBOS = [
    ("WR", "receiving_yards"),
    ("RB", "receiving_yards"),
    ("TE", "receiving_yards"),
    ("RB", "rushing_yards"),
    ("QB", "rushing_yards"),
    ("QB", "passing_yards"),
]

PLAYER_THRESHOLDS = [0.0, 0.04, 0.08, 0.12, 0.16, 0.20, 0.25, 0.30]
DEFENSE_THRESHOLDS = [0.0, 0.03, 0.05, 0.08]


def main():
    from dotenv import load_dotenv
    load_dotenv()

    from backtest.simulate_props import load_props_backtest_data, compute_synthetic_bets, measure_volatility, sweep_thresholds

    years = [2019, 2020, 2021, 2022, 2023, 2024]
    weekly_df = load_props_backtest_data(years)

    print("\n" + "=" * 70)
    print(f"PLAYER PROPS SYNTHETIC-LINE BACKTEST — {years[0]}-{years[-1]}")
    print("(Line = player's own trailing average. See caveat in the module docstring.)")
    print("=" * 70)

    for position, stat_column in COMBOS:
        logger.info(f"Computing {position} {stat_column}...")
        results = compute_synthetic_bets(weekly_df, position, stat_column)
        if results.empty:
            print(f"\n{position} {stat_column}: no data")
            continue

        vol = measure_volatility(results)
        print(f"\n--- {position} {stat_column} ---")
        print(f"  Player-weeks: {vol['n']}  |  natural volatility (stdev of deviation from own average): {vol['stdev_pct']}%")

        sweep = sweep_thresholds(results, PLAYER_THRESHOLDS, DEFENSE_THRESHOLDS)
        if sweep.empty:
            print("  No threshold combination produced enough flagged bets (min 20)")
            continue

        # Show the best few by hit rate, but only combos with a reasonable sample
        well_sampled = sweep[sweep["n"] >= 30].sort_values("hit_rate", ascending=False)
        print("  Best threshold combos (min 30 bets), by hit rate:")
        for _, r in well_sampled.head(5).iterrows():
            print(f"    player>={r['player_threshold']:.2f}  defense>={r['defense_threshold']:.2f}  "
                  f"-> n={int(r['n']):4d}  hit rate={r['hit_rate']*100:.1f}%")

    print()


if __name__ == "__main__":
    main()
