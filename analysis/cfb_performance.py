"""
Reusable performance-trend analysis for graded CFB spread/total picks. Built during the
2026-09-15 Weeks 1-2 review (see .claude/memory/sessions/2026-09-15-stats-fallback-and-
first-analysis.md for that review's findings) so the same checks can be re-run as more
weeks of data accumulate.

Not part of the live screener -- this reads the ledger after the fact. Typical use:

    from analysis.cfb_performance import load_graded_cfb, attach_predicted_margins

    df = load_graded_cfb("cfb_spread", season=2026)
    df = attach_predicted_margins(df)
    print(df["model_signed_error"].abs().mean(), df["market_signed_error"].abs().mean())
"""
import math
import re
import sqlite3

import pandas as pd

from screener.ledger import DB_PATH

# Matches the explanation text screen_cfb_spread (model/cfb_power_ratings.py) generates --
# e.g. "Model predicts Memphis wins by 22.6, vs. a market line implying a +10.8 home
# margin — 11.8 points of disagreement favors Memphis." This is the only way to recover the
# model's predicted margin and the market's implied margin for picks made before
# predicted_value/predicted_value_type existed (added 2026-09-15) -- only matches spread
# picks, since the phrasing is spread-specific.
_MARGIN_PATTERN = re.compile(r"wins by ([\d.]+), vs\. a market line implying a ([+-]?[\d.]+) home margin")


def load_graded_cfb(strategy="cfb_spread", season=None, weeks=None):
    """Every graded (won/lost) pick for one CFB strategy, optionally filtered to a season
    and/or a list of weeks."""
    conn = sqlite3.connect(DB_PATH)
    query = "SELECT * FROM picks WHERE strategy = ? AND status IN ('won','lost')"
    params = [strategy]
    if season is not None:
        query += " AND season = ?"
        params.append(season)
    if weeks is not None:
        placeholders = ",".join("?" for _ in weeks)
        query += f" AND week IN ({placeholders})"
        params.extend(weeks)
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    df["won"] = (df["status"] == "won").astype(int)
    df["actual_value"] = df["actual_value"].astype(float)
    return df


def attach_predicted_margins(df):
    """Parse the model's predicted margin and the market's implied margin (both re-oriented
    to the picked team's own perspective) out of each pick's explanation text, and compute
    each side's signed error against the real final margin. Rows whose explanation doesn't
    match the expected phrasing (e.g. total picks) come back with NaN in the new columns."""
    def parse_row(row):
        m = _MARGIN_PATTERN.search(row.get("explanation") or "")
        if not m:
            return pd.Series({"predicted_margin_picked": None, "market_margin_picked": None, "picked_is_home": None})
        predicted_for_picked = float(m.group(1))
        market_home_margin = float(m.group(2))
        picked_is_home = row["side"] == row["home_team"]
        market_for_picked = market_home_margin if picked_is_home else -market_home_margin
        return pd.Series({
            "predicted_margin_picked": predicted_for_picked,
            "market_margin_picked": market_for_picked,
            "picked_is_home": picked_is_home,
        })

    parsed = df.apply(parse_row, axis=1)
    df = pd.concat([df, parsed], axis=1)
    df["actual_margin_picked"] = df.apply(
        lambda r: r["actual_value"] if r["picked_is_home"] else -r["actual_value"], axis=1
    )
    df["model_signed_error"] = df["predicted_margin_picked"] - df["actual_margin_picked"]
    df["market_signed_error"] = df["market_margin_picked"] - df["actual_margin_picked"]
    return df


def historical_weekly_variance(burn_in_years, test_years, min_edge=4.0, min_sample=15):
    """How much do individual (season, week) win rates swing in the real backtest, purely
    from sample-size noise around the strategy's real long-run edge? This is what answers
    "is our current live shortfall actually unusual, or does the model's own history show
    stretches this rough as a matter of course" -- the check that showed 30% of historical
    weeks performed as badly or worse than a 42.9% live stretch.

    Runs the real backtest (needs CFBD_API_KEY in .env) -- takes a couple of minutes, so
    save the result (e.g. to CSV) if you'll want it again rather than re-running.
    """
    from backtest.simulate_cfb import run_cfb_backtest

    results = pd.DataFrame(run_cfb_backtest(burn_in_years=burn_in_years, test_years=test_years))
    spread = results[
        (results["market"] == "spread") & (results["edge_score"] >= min_edge) & (results["outcome"] != "push")
    ].copy()
    spread["won"] = (spread["outcome"] == "win").astype(int)
    by_week = spread.groupby(["season", "week"]).agg(n=("won", "count"), win_rate=("won", "mean")).reset_index()
    return by_week[by_week["n"] >= min_sample]


def live_vs_historical_significance(live_wins, live_n, backtest_win_rate):
    """Is a live sample's win rate statistically distinguishable from the backtest's
    documented long-run rate, treating the backtest rate as the true baseline? Returns
    (z, p_value); p >= 0.05 means "can't yet tell this apart from normal variance.\""""
    p_hat = live_wins / live_n
    se = math.sqrt(backtest_win_rate * (1 - backtest_win_rate) / live_n)
    z = (p_hat - backtest_win_rate) / se
    p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return z, p_value
