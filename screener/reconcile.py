import logging
import pandas as pd

from screener.ledger import get_open_picks, mark_result
from screener.fetch_stats import get_schedules, get_weekly_player_stats
from model.player_trends import PROP_STAT_MAP

logger = logging.getLogger(__name__)

GAME_MARKETS = {"spread", "total"}


def american_odds_profit(odds, stake=1.0):
    """Profit on a winning bet of `stake` units at American odds (does not include the stake) —
    same formula as the backtests, kept local here to avoid a cross-package import from backtest/."""
    if odds is None:
        return 0.0
    if odds > 0:
        return stake * odds / 100
    return stake * 100 / abs(odds)


def _find_game_result(schedules_df, season, week, home_team, away_team):
    """Actual final score for a completed game, or None if it hasn't been played yet."""
    match = schedules_df[
        (schedules_df["season"] == season) & (schedules_df["week"] == week)
        & (schedules_df["home_team"] == home_team) & (schedules_df["away_team"] == away_team)
    ]
    if match.empty:
        return None
    row = match.iloc[0]
    if pd.isna(row["home_score"]) or pd.isna(row["away_score"]):
        return None
    return row["home_score"], row["away_score"]


def grade_game_pick(pick, home_score, away_score):
    """Grade a spread or total pick against the actual final score, using the line we
    actually flagged at the time (not a re-fetched market line, which may have moved)."""
    if pick["market"] == "spread":
        home_margin = home_score - away_score
        required_home_margin = -pick["line"]  # market_line follows standard notation (negative = home favored)
        if pick["side"] == pick["home_team"]:
            if home_margin == required_home_margin:
                return "push"
            return "won" if home_margin > required_home_margin else "lost"
        else:
            if home_margin == required_home_margin:
                return "push"
            return "won" if home_margin < required_home_margin else "lost"

    if pick["market"] == "total":
        actual_total = home_score + away_score
        if actual_total == pick["line"]:
            return "push"
        if pick["side"] == "Over":
            return "won" if actual_total > pick["line"] else "lost"
        return "won" if actual_total < pick["line"] else "lost"

    return None


def _find_prop_actual(weekly_df, player_name, season, week, stat_column):
    """Actual stat value for a player in a given week, or None if not played/not published yet."""
    match = weekly_df[
        (weekly_df["player_display_name"] == player_name)
        & (weekly_df["season"] == season)
        & (weekly_df["week"] == week)
    ]
    if match.empty:
        return None
    value = match.iloc[0][stat_column]
    return None if pd.isna(value) else value


def grade_prop_pick(pick, actual_value):
    """Grade an Over/Under prop pick against the player's actual stat value that week."""
    if actual_value == pick["line"]:
        return "push"
    if pick["side"] == "Over":
        return "won" if actual_value > pick["line"] else "lost"
    return "won" if actual_value < pick["line"] else "lost"


def reconcile_all():
    """
    Check every open ledger pick against real results, and mark it won/lost/push wherever
    the underlying game (or that player's week) has actually completed. Safe to call on
    every run — picks with no result available yet are simply left open for next time.
    """
    open_picks = get_open_picks()
    seasons_needed = sorted({p["season"] for p in open_picks if p["season"] is not None})
    if not seasons_needed:
        return {"reconciled": 0, "still_open": len(open_picks)}

    try:
        schedules_df = get_schedules(seasons_needed)
    except RuntimeError:
        logger.info("No schedule data available yet for any open picks' seasons — nothing to reconcile.")
        return {"reconciled": 0, "still_open": len(open_picks)}

    try:
        weekly_df = get_weekly_player_stats(seasons_needed)
    except RuntimeError:
        # Normal early in a season — no player stats exist yet at all. Game picks (spread/
        # total) can still be reconciled from schedules_df alone; prop picks just wait.
        logger.info("No player stats available yet for any open picks' seasons — skipping prop reconciliation.")
        weekly_df = None

    reconciled = 0
    for pick in open_picks:
        if pick["season"] is None or pick["week"] is None:
            continue

        if pick["market"] in GAME_MARKETS:
            result = _find_game_result(schedules_df, pick["season"], pick["week"], pick["home_team"], pick["away_team"])
            if result is None:
                continue
            home_score, away_score = result
            outcome = grade_game_pick(pick, home_score, away_score)
            actual_value = home_score - away_score if pick["market"] == "spread" else home_score + away_score
        else:
            if weekly_df is None:
                continue
            stat_info = PROP_STAT_MAP.get(pick["market"])
            if stat_info is None:
                continue
            actual_value = _find_prop_actual(weekly_df, pick["subject"], pick["season"], pick["week"], stat_info["stat_column"])
            if actual_value is None:
                continue
            outcome = grade_prop_pick(pick, actual_value)

        if outcome is None:
            continue
        mark_result(pick["id"], outcome, actual_value)
        reconciled += 1

    return {"reconciled": reconciled, "still_open": len(open_picks) - reconciled}


def summarize_season(season=None):
    """Win rate and ROI per strategy for reconciled picks — the numbers behind the
    dashboard's season performance view."""
    from screener.ledger import get_all_picks

    picks = get_all_picks(season)
    decided = [p for p in picks if p["status"] in ("won", "lost")]
    if not decided:
        return {"overall": None, "by_strategy": {}}

    df = pd.DataFrame(decided)
    df["profit_units"] = df.apply(
        lambda p: american_odds_profit(p["price"]) if p["status"] == "won" else -1.0, axis=1
    )

    def summarize(subset):
        wins = (subset["status"] == "won").sum()
        total = len(subset)
        return {
            "bets": total,
            "wins": int(wins),
            "losses": int(total - wins),
            "win_rate": round(wins / total, 3) if total else None,
            "total_profit_units": round(subset["profit_units"].sum(), 2),
            "roi_pct": round(subset["profit_units"].sum() / total * 100, 1) if total else None,
        }

    overall = summarize(df)
    by_strategy = {strategy: summarize(group) for strategy, group in df.groupby("strategy")}
    return {"overall": overall, "by_strategy": by_strategy}
