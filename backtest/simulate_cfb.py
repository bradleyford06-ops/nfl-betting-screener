import logging
import pandas as pd

from screener.fetch_cfb_stats import get_cfb_schedules, get_cfb_betting_lines
from model.power_ratings import build_team_games
from model.cfb_power_ratings import (
    ratings_from_cfb_team_games,
    predict_cfb_matchup,
    screen_cfb_spread,
    screen_cfb_total,
    GAMES_WINDOW,
    RATING_ITERATIONS,
)

logger = logging.getLogger(__name__)

# cfbd's betting-lines endpoint gives real historical spreads/totals and moneylines, but
# (unlike nfl_data_py) doesn't return the actual price/vig booked for the spread and total
# sides themselves. Graded at the standard -110 both sides — the near-universal default
# across the industry when a specific book's price isn't available — so win rate here is
# exactly real, but ROI is an estimate under standard juice rather than an exact realized
# number the way the NFL backtest achieves.
STANDARD_ODDS = -110


def american_odds_profit(odds, stake=1.0):
    """Profit on a winning bet of `stake` units at American odds (does not include the stake itself)."""
    if odds > 0:
        return stake * odds / 100
    return stake * 100 / abs(odds)


def load_cfb_backtest_data(burn_in_years, test_years):
    """
    Load the full FBS schedule (burn-in + test years, used to build ratings — including
    FBS-vs-FCS games, since those are still real signal about an FBS team's own offense/
    defense) and real historical betting lines for just the test years (what predictions
    get graded against).
    """
    all_years = sorted(set(burn_in_years) | set(test_years))
    schedules = get_cfb_schedules(all_years)
    lines = get_cfb_betting_lines(test_years)
    return schedules, lines


def grade_spread_flag(flag, row):
    """Determine whether a flagged spread bet won, lost, or pushed against the actual score."""
    home_margin = row["home_score"] - row["away_score"]
    if home_margin == row["spread_line"]:
        return "push"
    home_covers = home_margin > row["spread_line"]
    if flag["side"] == row["home_team"]:
        return "win" if home_covers else "loss"
    return "loss" if home_covers else "win"


def grade_total_flag(flag, row):
    """Determine whether a flagged total bet won, lost, or pushed."""
    actual_total = row["home_score"] + row["away_score"]
    if actual_total == row["total_line"]:
        return "push"
    if flag["side"] == "Over":
        return "win" if actual_total > row["total_line"] else "loss"
    return "win" if actual_total < row["total_line"] else "loss"


GRADERS = {"spread": grade_spread_flag, "total": grade_total_flag}


def run_cfb_backtest(burn_in_years, test_years, games_window=GAMES_WINDOW, iterations=RATING_ITERATIONS):
    """
    Walk forward through past FBS seasons: for each test game, build team ratings using
    only games that happened before it (cached per season/week, since every game in the
    same week shares the same cutoff — recomputing per game would be ~4x more redundant
    work than the NFL backtest, given FBS has ~134 teams to the NFL's 32), generate a
    prediction, and grade every game's edge (threshold-free — every game gets a flag with
    its real edge_score) against what actually happened. Filtering by a specific edge
    threshold happens afterward, so one backtest run supports a full threshold sweep.

    Only FBS-vs-FBS games are graded — games against FCS opponents never get a prediction
    at all, since the FCS side has no rating of its own (see model.cfb_power_ratings).
    """
    schedules, lines = load_cfb_backtest_data(burn_in_years, test_years)
    schedules_completed = schedules.dropna(subset=["home_score", "away_score"])
    team_games = build_team_games(schedules_completed)
    fbs_teams = set(schedules.loc[schedules["home_classification"] == "fbs", "home_team"])

    test_games = lines[
        lines["home_team"].isin(fbs_teams) & lines["away_team"].isin(fbs_teams)
        & lines["season"].isin(test_years)
    ].dropna(subset=["home_score", "away_score", "spread_line", "total_line"]).sort_values(["season", "week"])

    logger.info(f"Backtesting {len(test_games)} FBS-vs-FBS games across seasons {sorted(test_years)}...")

    results = []
    ratings_cache = {}
    for _, row in test_games.iterrows():
        season, week = row["season"], row["week"]
        cache_key = (season, week)
        if cache_key not in ratings_cache:
            prior_games = team_games[
                (team_games["season"] < season) | ((team_games["season"] == season) & (team_games["week"] < week))
            ]
            ratings_cache[cache_key] = ratings_from_cfb_team_games(prior_games, fbs_teams, games_window, iterations)
        ratings = ratings_cache[cache_key]

        prediction = predict_cfb_matchup(ratings, row["home_team"], row["away_team"])
        if prediction is None:
            continue

        candidate_flags = [
            screen_cfb_spread(prediction, row["spread_line"], edge_threshold=0),
            screen_cfb_total(prediction, row["total_line"], edge_threshold=0),
        ]

        for flag in candidate_flags:
            if flag is None:
                continue
            outcome = GRADERS[flag["market"]](flag, row)
            profit = 0.0 if outcome == "push" else (american_odds_profit(STANDARD_ODDS) if outcome == "win" else -1.0)
            results.append({
                "season": season,
                "week": week,
                "matchup": f"{row['away_team']} @ {row['home_team']}",
                "market": flag["market"],
                "side": flag["side"],
                "edge_score": flag["edge_score"],
                "outcome": outcome,
                "profit_units": profit,
            })

    return results


def summarize_results(results, min_edge=0.0):
    """Turn graded bet records into a plain-English performance summary, overall and per
    market, restricted to bets whose edge_score meets min_edge — this is what lets one
    backtest run answer 'what if the live threshold were X?' for any X."""
    filtered = [r for r in results if r["edge_score"] >= min_edge]
    if not filtered:
        return {"overall": None, "by_market": {}}

    df = pd.DataFrame(filtered)
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
