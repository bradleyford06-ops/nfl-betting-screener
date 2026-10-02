#!/usr/bin/env python3
"""
Dives deeper than the plain --sweep in run_nhl_backtest.py: breaks the puck-line sweep
down by favorite vs. underdog side at each threshold. Needed because of a known
structural quirk (see model/nhl_power_ratings.py) — most NHL games are decided by one
goal, so blindly betting every underdog at +1.5 wins ~68% of all games with zero model
skill. A plain win-rate-vs-threshold sweep can look like "raising the threshold finds
real edge" when it's really just concentrating on the same free structural pattern.
Splitting by side shows whether raising the threshold ever surfaces real skill on the
favorite side (the side the model would actually have to be good to win), or just filters
down to a purer version of the same underdog-heavy mix.

Run: python -m backtest.run_nhl_puckline_sweep
"""

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

THRESHOLDS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]


def summarize(rows):
    if not rows:
        return None
    wins = sum(1 for r in rows if r["outcome"] == "win")
    total = len(rows)
    profit = sum(r["profit_units"] for r in rows)
    return {"n": total, "win_rate": wins / total, "roi_pct": profit / total * 100}


def fmt(s):
    if s is None:
        return f"{'n/a':>22}"
    return f"n={s['n']:4d}  win rate={s['win_rate']*100:5.1f}%  ROI={s['roi_pct']:+6.1f}%"


def main():
    from dotenv import load_dotenv
    load_dotenv()

    from backtest.simulate_nhl import run_nhl_backtest

    logger.info("Running NHL backtest (burn-in 2019, test 2021-2022 — the only real historical odds coverage)...")
    results = run_nhl_backtest([2019], [2021, 2022])
    puckline = [r for r in results if r["market"] == "puckline"]
    logger.info(f"{len(puckline)} total puck-line bets flagged across all thresholds (edge >= 0)")

    print("\n" + "=" * 100)
    print("NHL PUCK LINE — THRESHOLD SWEEP, BROKEN DOWN BY FAVORITE VS UNDERDOG SIDE")
    print("=" * 100)
    print(f"{'threshold':>10}  {'ALL PICKS':>28}  {'FAVORITE SIDE (-1.5)':>28}  {'UNDERDOG SIDE (+1.5)':>28}")

    for t in THRESHOLDS:
        at_threshold = [r for r in puckline if r["edge_score"] >= t and r["outcome"] != "push"]
        favorite_rows = [r for r in at_threshold if "+1.5" not in r["side"]]
        underdog_rows = [r for r in at_threshold if "+1.5" in r["side"]]

        overall = summarize(at_threshold)
        fav = summarize(favorite_rows)
        dog = summarize(underdog_rows)
        fav_share = f"({len(favorite_rows)}/{len(at_threshold)} fav)" if at_threshold else ""

        print(f"{t:>10.2f}  {fmt(overall):>28}  {fmt(fav):>28}  {fmt(dog):>28}  {fav_share}")

    print()


if __name__ == "__main__":
    main()
