import logging
import pandas as pd

from screener.fetch_nhl_stats import get_nhl_schedule, get_nhl_goalie_game_logs, get_nhl_historical_odds
from model.nhl_power_ratings import (
    compute_nhl_team_ratings, compute_goalie_ratings, predict_nhl_matchup,
    screen_nhl_moneyline, screen_nhl_puckline, screen_nhl_total, PUCK_LINE,
)

logger = logging.getLogger(__name__)

# The Kaggle historical odds dataset uses old-style short team codes for a handful of
# teams that the NHL API itself codes differently (its schedule data already reflects
# each historical season's real franchise code, e.g. ARI needs no mapping at all —
# only these five actually disagree).
ODDS_TEAM_ABBR_FIX = {"WAS": "WSH", "LA": "LAK", "NJ": "NJD", "TB": "TBL", "SJ": "SJS"}

ASSUMED_PUCKLINE_ODDS = -110  # no real per-game puck-line price in the free data — flat standard juice on both
                               # sides, same simplifying assumption already used for spreads/totals in the CFB backtest
ASSUMED_TOTAL_ODDS = -110


def fix_odds_team(abbr):
    return ODDS_TEAM_ABBR_FIX.get(abbr, abbr)


def american_odds_profit(odds, stake=1.0):
    """Profit on a winning bet of `stake` units at American odds (does not include the stake itself)."""
    if odds > 0:
        return stake * odds / 100
    return stake * 100 / abs(odds)


def load_backtest_odds(test_years):
    """Load and normalize the historical odds dataset for the given test season(s)."""
    odds = get_nhl_historical_odds(test_years)
    odds = odds.copy()
    odds["a__team"] = odds["a__team"].apply(fix_odds_team)
    odds["h__team"] = odds["h__team"].apply(fix_odds_team)
    odds["game_date"] = pd.to_datetime(odds["date"]).dt.strftime("%Y-%m-%d")
    return odds


def find_starting_goalies(goalie_game_logs, game_id):
    """
    The actual starting goalie for each side of a historical game — defined as whichever
    goalie logged the most ice time for that team in that game. Used only for backtesting,
    where the real outcome (who actually played) is already known; live screening instead
    falls back to a team's most-used recent goalie when no starter is confirmed yet.
    """
    game_rows = goalie_game_logs[goalie_game_logs["gameId"] == game_id]
    starters = {}
    for team, group in game_rows.groupby("playerTeam"):
        starters[team] = group.loc[group["icetime"].idxmax(), "playerId"]
    return starters


def run_nhl_backtest(burn_in_years, test_years, games_window=25):
    """
    Walk forward through real historical NHL seasons: for each test game, build team and
    goalie ratings using only data available before that game's date, generate a
    prediction using the two teams' actual starting goalies for that game, and grade
    every game's edge (threshold-free — every game gets a flag with its real edge_score)
    against the real historical odds. Filtering by a specific edge threshold happens
    afterward in summarize_results, so one backtest run supports a full threshold sweep
    or per-edge-bucket analysis, same pattern as the CFB/NFL backtests.
    """
    all_years = sorted(set(burn_in_years) | set(test_years))
    schedule = get_nhl_schedule(all_years).dropna(subset=["home_score", "away_score"])
    goalie_logs = get_nhl_goalie_game_logs(all_years)
    odds = load_backtest_odds(test_years)

    test_games = schedule[schedule["season"].isin(test_years)].sort_values("game_date")
    logger.info(f"Backtesting {len(test_games)} NHL games across seasons {sorted(test_years)}...")

    results = []
    for _, game in test_games.iterrows():
        prior_schedule = schedule[schedule["game_date"] < game["game_date"]]
        if len(prior_schedule) < 200:
            continue  # not enough games yet to build a meaningful rating (early burn-in period)

        odds_row = odds[
            (odds["game_date"] == game["game_date"])
            & (odds["h__team"] == game["home_team"])
            & (odds["a__team"] == game["away_team"])
        ]
        if odds_row.empty:
            continue  # no odds coverage for this game (outside the odds dataset's own season range)
        odds_row = odds_row.iloc[0]

        team_ratings = compute_nhl_team_ratings(prior_schedule, games_window)
        prior_goalie_logs = goalie_logs[goalie_logs["gameDate"] < int(game["game_date"].replace("-", ""))]
        goalie_ratings = compute_goalie_ratings(prior_goalie_logs, games_window)

        starters = find_starting_goalies(goalie_logs, game["game_id"])
        prediction = predict_nhl_matchup(
            team_ratings, goalie_ratings, game["home_team"], game["away_team"],
            home_goalie_id=starters.get(game["home_team"]), away_goalie_id=starters.get(game["away_team"]),
        )
        if prediction is None:
            continue

        home_ml, away_ml = (
            (odds_row["moneyline"], _implied_dog_price(odds_row["moneyline"])) if odds_row["fav"] == "Home"
            else (_implied_dog_price(odds_row["moneyline"]), odds_row["moneyline"]) if odds_row["fav"] == "Away"
            else (100, 100)  # 'Even' games — pick'em, no real favorite side
        )

        candidate_flags = [
            screen_nhl_moneyline(prediction, home_ml, away_ml, edge_threshold=0),
            screen_nhl_puckline(prediction, ASSUMED_PUCKLINE_ODDS, ASSUMED_PUCKLINE_ODDS, edge_threshold=0),
            screen_nhl_total(prediction, odds_row["over_under"], edge_threshold=0),
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
                "odds": odds_used,
                "model_prob": flag.get("model_win_prob", flag.get("model_cover_prob")),
                "market_prob": flag.get("market_implied_prob"),
                "outcome": outcome,
                "profit_units": profit,
            })

    return results


def _implied_dog_price(favorite_odds):
    """
    Approximate the underdog's moneyline price from the favorite's, since the free odds
    data only records the favorite's price. Assumes a standard ~5% sportsbook hold split
    evenly between both sides — a rough estimate, not the real quoted price.
    """
    favorite_implied = -favorite_odds / (-favorite_odds + 100)
    dog_implied = max(1 - favorite_implied - 0.05, 0.02)
    return round((1 - dog_implied) / dog_implied * 100) if dog_implied < 0.5 else round(-dog_implied / (1 - dog_implied) * 100)


def grade_flag(flag, game, odds_row):
    """Determine whether a flagged bet won, lost, or pushed against the real final score."""
    home_margin = game["home_score"] - game["away_score"]

    if flag["market"] == "moneyline":
        if home_margin == 0:
            return "push", 100
        winner = game["home_team"] if home_margin > 0 else game["away_team"]
        return ("win" if flag["side"] == winner else "loss"), flag["market_odds"]

    if flag["market"] == "puckline":
        home_covers = home_margin > PUCK_LINE  # home wins by 2+
        away_covers = home_margin < PUCK_LINE  # away doesn't lose by 2+ (i.e. loses by <=1 or wins outright) — the exact complement of home_covers
        if flag["side"].startswith(game["home_team"]):
            return ("win" if home_covers else "loss"), flag["market_odds"]
        else:
            return ("win" if away_covers else "loss"), flag["market_odds"]

    if flag["market"] == "total":
        actual_total = game["home_score"] + game["away_score"]
        if actual_total == odds_row["over_under"]:
            return "push", ASSUMED_TOTAL_ODDS
        covers_over = actual_total > odds_row["over_under"]
        return ("win" if (flag["side"] == "Over") == covers_over else "loss"), ASSUMED_TOTAL_ODDS

    raise ValueError(f"Unknown market: {flag['market']}")


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
