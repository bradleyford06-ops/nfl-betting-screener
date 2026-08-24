import logging
import pandas as pd

from screener.fetch_mlb_stats import get_mlb_schedule, get_mlb_historical_odds, get_pitcher_game_logs_for_schedule
from model.mlb_power_ratings import (
    compute_mlb_team_ratings, compute_park_factors, compute_pitcher_ratings, predict_mlb_matchup,
    screen_mlb_moneyline, screen_mlb_runline, screen_mlb_total, RUN_LINE,
)

logger = logging.getLogger(__name__)

# The Kaggle historical odds dataset uses "ARI"/Baseball-Reference-style codes; MLB's
# own API has always called Arizona "AZ" (not a recent rename, just a different
# convention).
ODDS_TEAM_ABBR_FIX = {"ARI": "AZ"}

# MLB's own /teams endpoint only returns each franchise's CURRENT name, so a historical
# schedule row still showing the old name (e.g. "Cleveland Indians" before their 2021
# rename to Guardians) doesn't match get_mlb_team_map()'s live-queried {name: abbr}
# dict at all -- found in testing 2026-08-23 when a full backtest season came back with
# zero gradable games. Applied as a fallback for schedule rows the live map misses.
HISTORICAL_TEAM_NAME_TO_ABBR = {
    "Cleveland Indians": "CLE",
    "Oakland Athletics": "OAK",
    "Florida Marlins": "MIA",
}

ASSUMED_RUNLINE_ODDS = -110  # runLineOdds is ~21% null in the free data -- flat standard juice when missing


def fix_odds_team(abbr):
    return ODDS_TEAM_ABBR_FIX.get(abbr, abbr)


def american_odds_profit(odds, stake=1.0):
    """Profit on a winning bet of `stake` units at American odds (does not include the stake itself)."""
    if odds > 0:
        return stake * odds / 100
    return stake * 100 / abs(odds)


def load_backtest_odds(test_years):
    """Load and normalize the historical odds dataset for the given test season(s)."""
    odds = get_mlb_historical_odds(test_years)
    odds = odds.copy()
    odds["team"] = odds["team"].apply(fix_odds_team)
    odds["opponent"] = odds["opponent"].apply(fix_odds_team)
    return odds


def run_mlb_backtest(burn_in_years, test_years, games_window=30, pitcher_games_window=8):
    """
    Walk forward through real historical MLB seasons: for each test game, build team,
    park, and pitcher ratings using only data available before that game's date,
    generate a prediction using the two probable starters MLB's own API recorded for
    that game, apply the live screening thresholds, and grade any flagged bets against
    the real historical odds. Uses MLB's own team abbreviation for the home/away key
    (the odds data is joined onto it, not the other way around) via the schedule's own
    team-name-to-abbreviation mapping done by the caller before this runs.
    """
    all_years = sorted(set(burn_in_years) | set(test_years))
    schedule = get_mlb_schedule(all_years).dropna(subset=["home_score", "away_score"])
    pitcher_logs = get_pitcher_game_logs_for_schedule(schedule)
    odds = load_backtest_odds(test_years)

    from screener.fetch_mlb_stats import get_mlb_team_map
    name_to_abbr = {**HISTORICAL_TEAM_NAME_TO_ABBR, **get_mlb_team_map()}  # live map takes precedence for any name in both
    schedule = schedule.copy()
    schedule["home_abbr"] = schedule["home_team"].map(name_to_abbr)
    schedule["away_abbr"] = schedule["away_team"].map(name_to_abbr)

    unmapped = set(schedule.loc[schedule["home_abbr"].isna(), "home_team"]) | set(schedule.loc[schedule["away_abbr"].isna(), "away_team"])
    if unmapped:
        logger.warning(f"No abbreviation mapping found for: {unmapped} — their games will be dropped from the backtest")

    test_games = schedule[schedule["season"].isin(test_years)].sort_values("game_date")
    logger.info(f"Backtesting {len(test_games)} MLB games across seasons {sorted(test_years)}...")

    results = []
    for _, game in test_games.iterrows():
        prior_schedule = schedule[schedule["game_date"] < game["game_date"]]
        if len(prior_schedule) < 300:
            continue  # not enough games yet to build a meaningful rating (early burn-in period)

        odds_row = odds[
            (odds["date"] == game["game_date"])
            & (odds["team"] == game["home_abbr"])
            & (odds["opponent"] == game["away_abbr"])
        ]
        if odds_row.empty:
            continue  # no odds coverage for this game
        odds_row = odds_row.iloc[0]

        team_ratings = compute_mlb_team_ratings(prior_schedule, games_window)
        park_factors, _ = compute_park_factors(prior_schedule)
        prior_pitcher_logs = pitcher_logs[pitcher_logs["date"] < game["game_date"]]
        pitcher_ratings, _ = compute_pitcher_ratings(prior_pitcher_logs, pitcher_games_window)

        # Team/pitcher ratings are keyed by full team name (what the schedule and
        # pitcher logs both use) -- home_abbr/away_abbr exist only to join the
        # abbreviation-keyed odds data above, not as the model's own team key.
        prediction = predict_mlb_matchup(
            team_ratings, pitcher_ratings, park_factors, game["home_team"], game["away_team"],
            venue_name=game["venue_name"], home_pitcher_id=game.get("home_pitcher_id"), away_pitcher_id=game.get("away_pitcher_id"),
        )
        if prediction is None:
            continue

        home_ml, away_ml = odds_row["moneyLine"], odds_row["oppMoneyLine"]
        home_rl_odds = odds_row["runLineOdds"] if pd.notna(odds_row["runLineOdds"]) else ASSUMED_RUNLINE_ODDS
        away_rl_odds = odds_row["oppRunLineOdds"] if pd.notna(odds_row["oppRunLineOdds"]) else ASSUMED_RUNLINE_ODDS

        candidate_flags = [
            screen_mlb_moneyline(prediction, home_ml, away_ml),
            screen_mlb_runline(prediction, home_rl_odds, away_rl_odds),
            screen_mlb_total(prediction, odds_row["total"]),
        ]

        for flag in candidate_flags:
            if flag is None:
                continue
            outcome, odds_used = grade_flag(flag, game, odds_row)
            profit = 0.0 if outcome == "push" else (american_odds_profit(odds_used) if outcome == "win" else -1.0)
            results.append({
                "season": game["season"],
                "game_date": game["game_date"],
                "matchup": f"{game['away_team']} @ {game['home_team']}",
                "market": flag["market"],
                "side": flag["side"],
                "edge_score": flag["edge_score"],
                "outcome": outcome,
                "profit_units": profit,
            })

    return results


def grade_flag(flag, game, odds_row):
    """Determine whether a flagged bet won, lost, or pushed against the real final score."""
    home_margin = game["home_score"] - game["away_score"]

    if flag["market"] == "moneyline":
        if home_margin == 0:
            return "push", 100
        winner = game["home_team"] if home_margin > 0 else game["away_team"]
        return ("win" if flag["side"] == winner else "loss"), flag["market_odds"]

    if flag["market"] == "runline":
        home_covers = home_margin > RUN_LINE
        away_covers = home_margin < RUN_LINE  # exact complement -- run margins are integers, same reasoning as the NHL puck-line fix
        if flag["side"].startswith(game["home_team"]):
            return ("win" if home_covers else "loss"), flag["market_odds"]
        else:
            return ("win" if away_covers else "loss"), flag["market_odds"]

    if flag["market"] == "total":
        actual_total = game["home_score"] + game["away_score"]
        if actual_total == odds_row["total"]:
            return "push", -110
        covers_over = actual_total > odds_row["total"]
        over_odds = odds_row["overOdds"] if pd.notna(odds_row["overOdds"]) else -110
        under_odds = odds_row["underOdds"] if pd.notna(odds_row["underOdds"]) else -110
        odds_used = over_odds if flag["side"] == "Over" else under_odds
        return ("win" if (flag["side"] == "Over") == covers_over else "loss"), odds_used

    raise ValueError(f"Unknown market: {flag['market']}")


def summarize_results(results, min_edge=0.0):
    """Turn graded bet records into a plain-English performance summary, overall and per
    market, restricted to bets whose edge_score meets min_edge."""
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
