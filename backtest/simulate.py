import logging
import pandas as pd

from screener.fetch_stats import get_schedules
from model.power_ratings import (
    build_team_games,
    ratings_from_team_games,
    predict_matchup,
    screen_spread,
    screen_total,
    screen_moneyline,
)

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "home_score", "away_score", "spread_line", "total_line",
    "home_moneyline", "away_moneyline",
    "home_spread_odds", "away_spread_odds", "over_odds", "under_odds",
]


def american_odds_profit(odds, stake=1.0):
    """Profit on a winning bet of `stake` units at American odds (does not include the stake itself)."""
    if odds > 0:
        return stake * odds / 100
    return stake * 100 / abs(odds)


def load_backtest_data(burn_in_years, test_years):
    """
    Load historical schedules (with closing lines and actual odds) for both the
    burn-in period (used only to warm up team ratings) and the test period (the
    games we'll actually generate predictions for and grade).
    """
    all_years = sorted(set(burn_in_years) | set(test_years))
    schedules = get_schedules(all_years)
    schedules = schedules[schedules["game_type"] == "REG"].copy()
    schedules = schedules.dropna(subset=REQUIRED_COLUMNS)
    return schedules


def grade_spread_flag(flag, row):
    """Determine whether a flagged spread bet won, lost, or pushed against the actual score,
    and what it would have paid at the actual historical odds for that side."""
    home_margin = row["home_score"] - row["away_score"]
    home_covers = home_margin > row["spread_line"]
    away_covers = home_margin < row["spread_line"]

    if flag["side"] == row["home_team"]:
        odds = row["home_spread_odds"]
        if home_margin == row["spread_line"]:
            return "push", odds
        return ("win" if home_covers else "loss"), odds
    else:
        odds = row["away_spread_odds"]
        if home_margin == row["spread_line"]:
            return "push", odds
        return ("win" if away_covers else "loss"), odds


def grade_total_flag(flag, row):
    """Determine whether a flagged total bet won, lost, or pushed."""
    actual_total = row["home_score"] + row["away_score"]
    if flag["side"] == "Over":
        odds = row["over_odds"]
        if actual_total == row["total_line"]:
            return "push", odds
        return ("win" if actual_total > row["total_line"] else "loss"), odds
    else:
        odds = row["under_odds"]
        if actual_total == row["total_line"]:
            return "push", odds
        return ("win" if actual_total < row["total_line"] else "loss"), odds


def grade_moneyline_flag(flag, row):
    """Determine whether a flagged moneyline bet won or lost (NFL ties push)."""
    if row["home_score"] == row["away_score"]:
        odds = row["home_moneyline"] if flag["side"] == row["home_team"] else row["away_moneyline"]
        return "push", odds

    winner = row["home_team"] if row["home_score"] > row["away_score"] else row["away_team"]
    odds = row["home_moneyline"] if flag["side"] == row["home_team"] else row["away_moneyline"]
    return ("win" if flag["side"] == winner else "loss"), odds


GRADERS = {"spread": grade_spread_flag, "total": grade_total_flag, "moneyline": grade_moneyline_flag}


def run_backtest(burn_in_years, test_years, games_window=17):
    """
    Walk forward through past seasons: for each test game, build team ratings using
    only games that happened before it, generate a prediction, apply the live
    screening thresholds, and grade any flagged bets against what actually happened.
    Returns a list of graded bet records.
    """
    schedules = load_backtest_data(burn_in_years, test_years)
    team_games = build_team_games(schedules)

    test_games = schedules[schedules["season"].isin(test_years)].sort_values(["season", "week"])
    logger.info(f"Backtesting {len(test_games)} games across seasons {sorted(test_years)}...")

    results = []
    for _, row in test_games.iterrows():
        season, week = row["season"], row["week"]
        prior_games = team_games[
            (team_games["season"] < season) | ((team_games["season"] == season) & (team_games["week"] < week))
        ]
        ratings = ratings_from_team_games(prior_games, games_window)
        prediction = predict_matchup(ratings, row["home_team"], row["away_team"])
        if prediction is None:
            continue

        candidate_flags = [
            screen_spread(prediction, -row["spread_line"]),
            screen_total(prediction, row["total_line"]),
            screen_moneyline(prediction, row["home_moneyline"], row["away_moneyline"]),
        ]

        for flag in candidate_flags:
            if flag is None:
                continue
            outcome, odds = GRADERS[flag["market"]](flag, row)
            profit = 0.0 if outcome == "push" else (american_odds_profit(odds) if outcome == "win" else -1.0)
            results.append({
                "season": season,
                "week": week,
                "matchup": f"{row['away_team']} @ {row['home_team']}",
                "market": flag["market"],
                "side": flag["side"],
                "edge_score": flag["edge_score"],
                "odds": odds,
                "outcome": outcome,
                "profit_units": profit,
            })

    return results


def summarize_results(results):
    """Turn graded bet records into a plain-English performance summary, overall and per market."""
    if not results:
        return {"overall": None, "by_market": {}}

    df = pd.DataFrame(results)
    decided = df[df["outcome"] != "push"]

    def summarize(subset):
        wins = (subset["outcome"] == "win").sum()
        total = len(subset)
        return {
            "bets": total,
            "wins": int(wins),
            "losses": int(total - wins),
            "win_rate": round(wins / total, 3) if total else None,
            "total_profit_units": round(subset["profit_units"].sum(), 2),
            "roi_pct": round(subset["profit_units"].sum() / total * 100, 1) if total else None,
        }

    overall = summarize(decided)
    by_market = {market: summarize(group) for market, group in decided.groupby("market")}

    return {"overall": overall, "by_market": by_market}
