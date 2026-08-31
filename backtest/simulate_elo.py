import logging
import pandas as pd

from screener.fetch_stats import get_schedules, get_weekly_player_stats
from model.nfl_elo_ratings import build_elo_history
from model.qb_adjustment import build_qb_adjustments
from model.power_ratings import american_odds_to_implied_prob, devig_two_way
from backtest.simulate import american_odds_profit

logger = logging.getLogger(__name__)


def load_elo_history(burn_in_years, test_years, use_qb_adjustment=True, qb_elo_scale=None):
    """
    Build the full Elo rating history (burn-in years included, purely to let ratings converge
    before the test period starts) in one forward pass. QB adjustments, if enabled, are
    computed from the same combined year range so a QB's trailing rating carries in from
    games played during burn-in, not reset to zero right when the test period begins.
    """
    all_years = sorted(set(burn_in_years) | set(test_years))
    schedules = get_schedules(all_years)
    schedules = schedules[schedules["game_type"].isin(["REG", "WC", "DIV", "CON", "SB"])].copy()

    qb_adjustments = None
    if use_qb_adjustment:
        weekly = get_weekly_player_stats(all_years)
        kwargs = {"elo_scale": qb_elo_scale} if qb_elo_scale is not None else {}
        qb_adjustments = build_qb_adjustments(schedules, weekly, **kwargs)

    history_df, final_ratings = build_elo_history(schedules, qb_adjustments=qb_adjustments)
    return history_df, final_ratings


def grade_elo_history(history_df, test_years):
    """
    Grade every regular-season test-period game's moneyline pick (threshold-free — every
    game gets a record with its real edge_score) against what actually happened, using real
    historical moneyline odds. Filtering by a specific edge threshold happens afterward in
    summarize_results, same pattern as the other backtests in this project.
    """
    test_games = history_df[
        history_df["season"].isin(test_years) & (history_df["game_type"] == "REG")
    ].copy()
    test_games = test_games.dropna(subset=["home_moneyline", "away_moneyline", "home_score", "away_score"])

    results = []
    for row in test_games.itertuples():
        if row.home_score == row.away_score:
            continue  # NFL ties can't be graded win/loss on a two-way moneyline

        home_implied = american_odds_to_implied_prob(row.home_moneyline)
        away_implied = american_odds_to_implied_prob(row.away_moneyline)
        home_fair, away_fair = devig_two_way(home_implied, away_implied)

        home_edge = row.home_win_prob - home_fair
        if home_edge > 0:
            side, odds, edge, market_prob = "home", row.home_moneyline, home_edge, home_fair
        else:
            side, odds, edge, market_prob = "away", row.away_moneyline, -home_edge, away_fair

        winner = "home" if row.home_score > row.away_score else "away"
        outcome = "win" if side == winner else "loss"
        profit = american_odds_profit(odds) if outcome == "win" else -1.0

        results.append({
            "season": row.season,
            "week": row.week,
            "matchup": f"{row.away_team} @ {row.home_team}",
            "side": side,
            "model_win_prob": round(row.home_win_prob if side == "home" else 1 - row.home_win_prob, 3),
            "market_prob": round(market_prob, 3),
            "edge_score": round(edge, 3),
            "odds": odds,
            "outcome": outcome,
            "profit_units": profit,
        })

    return results


def calibration_summary(history_df, test_years):
    """Brier score (mean squared error of predicted vs. actual outcome — lower is better) for
    the model vs. the market's own devigged probability, over the same graded test games. This
    is what actually shows whether the model's win-probability estimate is any good, separate
    from whether a specific edge threshold happens to be profitable."""
    test_games = history_df[
        history_df["season"].isin(test_years) & (history_df["game_type"] == "REG")
    ].copy()
    test_games = test_games.dropna(subset=["home_moneyline", "away_moneyline", "home_score", "away_score"])
    test_games = test_games[test_games["home_score"] != test_games["away_score"]]

    home_implied = test_games["home_moneyline"].apply(american_odds_to_implied_prob)
    away_implied = test_games["away_moneyline"].apply(american_odds_to_implied_prob)
    market_home_prob = pd.Series(
        [devig_two_way(h, a)[0] for h, a in zip(home_implied, away_implied)],
        index=test_games.index,
    )
    home_won = (test_games["home_score"] > test_games["away_score"]).astype(float)

    model_brier = ((test_games["home_win_prob"] - home_won) ** 2).mean()
    market_brier = ((market_home_prob - home_won) ** 2).mean()
    return {"n": len(test_games), "model_brier": round(model_brier, 4), "market_brier": round(market_brier, 4)}


def summarize_elo_results(results, min_edge=0.0):
    """Turn graded moneyline records into a plain win-rate/ROI summary, restricted to bets
    whose edge_score meets min_edge — lets one backtest run answer 'what if the live
    threshold were X?' for any X without re-simulating."""
    filtered = [r for r in results if r["edge_score"] >= min_edge]
    if not filtered:
        return None

    df = pd.DataFrame(filtered)
    wins = (df["outcome"] == "win").sum()
    total = len(df)
    return {
        "bets": total,
        "wins": int(wins),
        "losses": int(total - wins),
        "win_rate": round(wins / total, 3) if total else None,
        "total_profit_units": round(df["profit_units"].sum(), 2),
        "roi_pct": round(df["profit_units"].sum() / total * 100, 1) if total else None,
    }
