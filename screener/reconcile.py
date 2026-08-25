import logging
import pandas as pd

from screener.ledger import get_open_picks, mark_result
from screener.fetch_stats import get_schedules, get_weekly_player_stats
from screener.fetch_cfb_stats import get_cfb_schedules
from screener.fetch_mlb_stats import get_mlb_schedule
from model.player_trends import PROP_STAT_MAP

logger = logging.getLogger(__name__)

GAME_MARKETS = {"spread", "total", "moneyline", "runline"}


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

    if pick["market"] == "moneyline":
        if home_score == away_score:
            return "push"  # essentially never happens in a completed MLB game, handled defensively anyway
        winner = pick["home_team"] if home_score > away_score else pick["away_team"]
        return "won" if pick["side"] == winner else "lost"

    if pick["market"] == "runline":
        # The run line is always +/-1.5 (see model/mlb_power_ratings.py's RUN_LINE) --
        # margins are integers, so home/away covering are exact complements, same
        # reasoning as the NHL puck-line grading fix.
        home_margin = home_score - away_score
        if pick["side"].startswith(pick["home_team"]):
            return "won" if home_margin > 1.5 else "lost"
        return "won" if home_margin < 1.5 else "lost"

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
        logger.info("No NFL schedule data available yet for any open picks' seasons — skipping NFL game reconciliation.")
        schedules_df = None

    cfb_error = None
    try:
        cfb_schedules_df = get_cfb_schedules(seasons_needed)
    except RuntimeError:
        # cfbd itself reports no data published yet — normal early in a season, not a
        # real failure, so no alert.
        logger.info("No CFB schedule data available yet — skipping CFB game reconciliation.")
        cfb_schedules_df = None
    except Exception as e:
        # Anything else (bad/missing API key, cfbd outage, etc.) is a real failure, not
        # an expected "not published yet" gap — same alerting reasoning as the CFB
        # screening failure in screener/pipeline.py: this is caught here so it can't take
        # down NFL reconciliation, but that means it's otherwise invisible.
        logger.error(f"CFB schedule fetch failed, skipping CFB game reconciliation: {e}")
        cfb_schedules_df = None
        cfb_error = str(e)

    mlb_error = None
    try:
        mlb_schedules_df = get_mlb_schedule(seasons_needed)
        # MLB picks use the game's calendar date (YYYYMMDD) as "week" instead of a real
        # week number (see run_mlb_game_screener in screener/pipeline.py) -- add the same
        # encoding here so _find_game_result's generic season/week/team match works.
        mlb_schedules_df = mlb_schedules_df.assign(
            week=mlb_schedules_df["game_date"].str.replace("-", "", regex=False).astype(int)
        )
    except RuntimeError:
        logger.info("No MLB schedule data available yet — skipping MLB game reconciliation.")
        mlb_schedules_df = None
    except Exception as e:
        # Same reasoning as the CFB block above -- a real failure (not just "not
        # published yet") needs to be visible, not just logged and skipped.
        logger.error(f"MLB schedule fetch failed, skipping MLB game reconciliation: {e}")
        mlb_schedules_df = None
        mlb_error = str(e)

    if schedules_df is None and cfb_schedules_df is None and mlb_schedules_df is None:
        return {"reconciled": 0, "still_open": len(open_picks), "cfb_error": cfb_error, "mlb_error": mlb_error}

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
            if pick["strategy"].startswith("cfb_"):
                active_schedule = cfb_schedules_df
            elif pick["strategy"].startswith("mlb_"):
                active_schedule = mlb_schedules_df
            elif pick["strategy"].startswith("nhl_"):
                active_schedule = None  # NHL reconciliation isn't built yet
            else:
                active_schedule = schedules_df
            if active_schedule is None:
                continue
            result = _find_game_result(active_schedule, pick["season"], pick["week"], pick["home_team"], pick["away_team"])
            if result is None:
                continue
            home_score, away_score = result
            outcome = grade_game_pick(pick, home_score, away_score)
            actual_value = home_score + away_score if pick["market"] == "total" else home_score - away_score
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

    return {"reconciled": reconciled, "still_open": len(open_picks) - reconciled, "cfb_error": cfb_error, "mlb_error": mlb_error}


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
        result = {
            "bets": total,
            "wins": int(wins),
            "losses": int(total - wins),
            "win_rate": round(wins / total, 3) if total else None,
            "total_profit_units": round(subset["profit_units"].sum(), 2),
            "roi_pct": round(subset["profit_units"].sum() / total * 100, 1) if total else None,
        }
        return result

    overall = summarize(df)
    by_strategy = {strategy: summarize(group) for strategy, group in df.groupby("strategy")}
    return {"overall": overall, "by_strategy": by_strategy}
