import logging
import pandas as pd
from datetime import datetime

from screener.fetch_stats import get_weekly_player_stats, get_schedules
from screener.fetch_pbp import get_play_by_play
from screener.fetch_odds import get_events, get_game_odds, get_player_props
from screener.team_map import get_team_name_to_abbr, to_abbr
from screener.scoring import rank_props, rank_games
from model.power_ratings import compute_team_ratings, predict_matchup, screen_spread, screen_total
from model.player_trends import (
    screen_player_prop, player_current_team, has_nfl_history,
    get_position_stat_ratings, player_adjusted_average,
)
from model.coverage_sim import (
    team_defensive_tendencies, player_coverage_splits, screen_simplified_coverage_prop, COVERAGE_MARKET_MAP,
)

logger = logging.getLogger(__name__)


def lookup_season_week(schedules_df, home_abbr, away_abbr):
    """
    Find the season/week for an upcoming (not-yet-played) matchup, so the ledger can record
    which NFL week a pick belongs to and reconciliation knows when the game happens.
    Returns (None, None) if no matching upcoming game is found.
    """
    matches = schedules_df[
        (schedules_df["home_team"] == home_abbr)
        & (schedules_df["away_team"] == away_abbr)
        & (schedules_df["home_score"].isna())
    ]
    if matches.empty:
        return None, None
    row = matches.sort_values(["season", "week"]).iloc[0]
    return int(row["season"]), int(row["week"])


def get_current_week(schedules_df):
    """
    Find the nearest upcoming NFL week — the earliest (season, week) that still has at
    least one unplayed game. The odds API returns odds for every remaining game in the
    season in one call, so screening needs this to restrict results to just the next
    slate rather than flagging edges across the whole rest of the year.
    """
    unplayed = schedules_df[schedules_df["home_score"].isna()]
    if unplayed.empty:
        return None, None
    row = unplayed.sort_values(["season", "week"]).iloc[0]
    return int(row["season"]), int(row["week"])


def get_stats_years():
    """Which seasons to pull stats for. Requests the last 3 years — any year whose data
    hasn't been published yet (including the current season before it starts) is
    automatically skipped by the fetch layer, so this always falls back to real data."""
    year = datetime.now().year
    return [year - 2, year - 1, year]


def run_game_screener(schedules_df, name_map, current_season, current_week, markets="spreads,totals"):
    """
    Screen the upcoming week's NFL games' spread and total against our power-rating model.

    Moneyline screening is intentionally left out: a backtest against 2019-2024 (see
    backtest/run_backtest.py) showed it lost -20.8% ROI, because the model's win
    probabilities never got as extreme as the market's on lopsided games, so it kept
    betting big underdogs against teams that were favored for real reasons. Re-enable
    once the model has been re-backtested and shown to be profitable on moneylines.
    """
    ratings = compute_team_ratings(schedules_df)
    games = get_game_odds(markets=markets)
    flags = []

    for game in games:
        home_full, away_full = game["home_team"], game["away_team"]
        home_abbr, away_abbr = to_abbr(home_full, name_map), to_abbr(away_full, name_map)

        season, week = lookup_season_week(schedules_df, home_abbr, away_abbr)
        if current_week is not None and (season, week) != (current_season, current_week):
            continue  # only the upcoming week's slate — the odds API returns the whole season at once

        prediction = predict_matchup(ratings, home_abbr, away_abbr)
        if prediction is None:
            logger.debug(f"Skipping {away_full} @ {home_full} — not enough rating data yet")
            continue

        spread_lines, spread_prices, total_lines, total_prices = [], [], [], []
        for bookmaker in game.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market["key"] == "spreads":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == home_full:
                            spread_lines.append(outcome["point"])
                            spread_prices.append(outcome["price"])
                elif market["key"] == "totals":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == "Over":
                            total_lines.append(outcome["point"])
                            total_prices.append(outcome["price"])

        game_context = {
            "home_team": home_abbr,
            "away_team": away_abbr,
            "commence_time": game.get("commence_time"),
            "season": season,
            "week": week,
        }

        if spread_lines:
            flag = screen_spread(prediction, sum(spread_lines) / len(spread_lines))
            if flag:
                flag["price"] = sum(spread_prices) / len(spread_prices)
                flags.append({**flag, **game_context})

        if total_lines:
            flag = screen_total(prediction, sum(total_lines) / len(total_lines))
            if flag:
                flag["price"] = sum(total_prices) / len(total_prices)
                flags.append({**flag, **game_context})

    return rank_games(flags)


def run_props_screener(weekly_df, schedules_df, name_map, current_season, current_week, games_window=8):
    """
    Screen the upcoming week's player prop lines against the player-vs-defense trend model.

    Players with zero NFL game history (true rookie debuts) can't have a trend computed —
    rather than silently vanishing them, they're collected into a separate "no data yet"
    list so Bradley can see which rookie props exist even though the model has no opinion.

    Some stat/position combos (currently just QB rushing yards) showed weak, noisy signal
    in backtesting and are marked "speculative" by screen_player_prop — kept live for
    visibility rather than filtered out, but routed to their own list here so they don't
    get mixed into the main ranked results at face value.
    """
    events = get_events()
    flags = []
    speculative_flags = []
    no_data = []
    ratings_cache = {}  # shared across all players in this run so shared position/stat combos aren't recomputed

    for event in events:
        home_full, away_full = event["home_team"], event["away_team"]
        home_abbr, away_abbr = to_abbr(home_full, name_map), to_abbr(away_full, name_map)

        season, week = lookup_season_week(schedules_df, home_abbr, away_abbr)
        if current_week is not None and (season, week) != (current_season, current_week):
            continue  # only the upcoming week — skip the props fetch entirely to save API quota

        try:
            props = get_player_props(event["id"])
        except Exception as e:
            logger.warning(f"Failed to fetch props for {away_full} @ {home_full}: {e}")
            continue

        # Average each player's line/price across bookmakers before screening, same as game odds
        lines_by_player_market = {}
        prices_by_player_market = {}
        for bookmaker in props.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                market_key = market["key"]
                for outcome in market["outcomes"]:
                    if outcome["name"] != "Over":
                        continue
                    player_name = outcome["description"]
                    key = (market_key, player_name)
                    lines_by_player_market.setdefault(key, []).append(outcome["point"])
                    prices_by_player_market.setdefault(key, []).append(outcome["price"])

        for (market_key, player_name), lines in lines_by_player_market.items():
            avg_line = sum(lines) / len(lines)
            prices = prices_by_player_market[(market_key, player_name)]
            avg_price = sum(prices) / len(prices)

            if not has_nfl_history(weekly_df, player_name):
                no_data.append({
                    "player": player_name,
                    "market": market_key,
                    "line": avg_line,
                    "matchup": f"{away_abbr} @ {home_abbr}",
                })
                continue

            player_team = player_current_team(weekly_df, player_name)
            if player_team == home_abbr:
                opponent = away_abbr
            elif player_team == away_abbr:
                opponent = home_abbr
            else:
                continue  # has history, but team doesn't match this game — skip rather than guess

            flag = screen_player_prop(weekly_df, player_name, market_key, opponent, avg_line, games_window, ratings_cache)
            if flag:
                flag.update({
                    "price": avg_price, "season": season, "week": week,
                    "home_team": home_abbr, "away_team": away_abbr, "commence_time": event.get("commence_time"),
                })
                (speculative_flags if flag["speculative"] else flags).append(flag)

    return rank_props(flags), rank_props(speculative_flags), no_data


def run_coverage_screener(weekly_df, pbp_df, schedules_df, name_map, current_season, current_week, games_window=8):
    """
    Screen the upcoming week's receiving props (receptions, receiving yards only — the
    underlying data is specifically about pass coverage) with the coverage-simulation
    model: a player's own opponent-adjusted target volume times a zone/man efficiency
    modifier based on this week's specific opponent's coverage tendency.

    Runs as a second, independent signal alongside the trend model (run_props_screener) —
    backtested separately (backtest/simulate_coverage_v2.py) and validated on its own
    terms, not blended with or required to agree with the trend model's picks. Bradley
    chose to see both signals separately rather than merge them, so results land in their
    own report section rather than the main props list.
    """
    events = get_events()
    flags = []
    target_ratings_cache = {}
    def_tendencies = team_defensive_tendencies(pbp_df)

    for event in events:
        home_full, away_full = event["home_team"], event["away_team"]
        home_abbr, away_abbr = to_abbr(home_full, name_map), to_abbr(away_full, name_map)

        season, week = lookup_season_week(schedules_df, home_abbr, away_abbr)
        if current_week is not None and (season, week) != (current_season, current_week):
            continue  # only the upcoming week — same cache key as run_props_screener, so this still costs nothing extra

        try:
            props = get_player_props(event["id"])  # same default markets as run_props_screener — cache hit, no extra API cost
        except Exception as e:
            logger.warning(f"Failed to fetch props for {away_full} @ {home_full}: {e}")
            continue

        lines_by_player_market = {}
        prices_by_player_market = {}
        for bookmaker in props.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                market_key = market["key"]
                if market_key not in COVERAGE_MARKET_MAP:
                    continue
                for outcome in market["outcomes"]:
                    if outcome["name"] != "Over":
                        continue
                    player_name = outcome["description"]
                    key = (market_key, player_name)
                    lines_by_player_market.setdefault(key, []).append(outcome["point"])
                    prices_by_player_market.setdefault(key, []).append(outcome["price"])

        for (market_key, player_name), lines in lines_by_player_market.items():
            avg_line = sum(lines) / len(lines)
            avg_price = sum(prices_by_player_market[(market_key, player_name)]) / len(prices_by_player_market[(market_key, player_name)])

            player_rows = weekly_df[weekly_df["player_display_name"] == player_name]
            if player_rows.empty:
                continue  # no history — already surfaced by the trend model's "no data yet" list
            player_id = player_rows["player_id"].iloc[-1]
            position = player_rows["position"].iloc[-1]

            player_team = player_current_team(weekly_df, player_name)
            if player_team == home_abbr:
                opponent = away_abbr
            elif player_team == away_abbr:
                opponent = home_abbr
            else:
                continue

            target_ratings = get_position_stat_ratings(weekly_df, position, "targets", target_ratings_cache)
            player_avg_targets, _ = player_adjusted_average(weekly_df, player_name, "targets", target_ratings, games_window)
            if player_avg_targets is None:
                continue

            opponent_row = def_tendencies[def_tendencies["team"] == opponent]
            if opponent_row.empty:
                continue
            zone_rate = opponent_row["avg_zone_rate"].iloc[0]
            if pd.isna(zone_rate):
                continue

            coverage_splits = player_coverage_splits(pbp_df, player_id)
            flag = screen_simplified_coverage_prop(player_name, market_key, opponent, player_avg_targets, zone_rate, coverage_splits, avg_line)
            if flag:
                flag.update({
                    "price": avg_price, "season": season, "week": week,
                    "home_team": home_abbr, "away_team": away_abbr, "commence_time": event.get("commence_time"),
                })
                flags.append(flag)

    return rank_props(flags)


def run_screener(props_only=False, games_only=False):
    """Run the full NFL betting screener: fetch data, run all models, return ranked results."""
    years = get_stats_years()
    weekly_df = get_weekly_player_stats(years)
    schedules_df = get_schedules(years)
    name_map = get_team_name_to_abbr()

    current_season, current_week = get_current_week(schedules_df)
    if current_week is None:
        logger.warning("Could not determine the upcoming NFL week from the schedule — screening every game found instead of just the next slate")
    else:
        logger.info(f"Screening for {current_season} week {current_week}")

    game_flags = [] if props_only else run_game_screener(schedules_df, name_map, current_season, current_week)

    if games_only:
        prop_flags, prop_speculative, prop_no_data, prop_coverage = [], [], [], []
    else:
        prop_flags, prop_speculative, prop_no_data = run_props_screener(weekly_df, schedules_df, name_map, current_season, current_week)
        pbp_df = get_play_by_play(years)
        prop_coverage = run_coverage_screener(weekly_df, pbp_df, schedules_df, name_map, current_season, current_week)

    return {
        "games": game_flags,
        "props": prop_flags,
        "props_speculative": prop_speculative,
        "props_coverage": prop_coverage,
        "props_no_data": prop_no_data,
    }


def log_results_to_ledger(results):
    """
    Record every currently-flagged pick into the permanent performance ledger, so it can be
    reconciled against real results later and rolled up into a season-long track record.
    Picks without a resolved season/week (shouldn't normally happen for real games, but
    guards against it) are skipped rather than logged with missing keys.
    """
    from screener.ledger import record_pick

    def log_flag(flag, strategy, subject):
        if flag.get("season") is None or flag.get("week") is None:
            logger.debug(f"Skipping ledger entry for {subject} — no season/week resolved")
            return
        record_pick(
            strategy=strategy,
            season=flag["season"],
            week=flag["week"],
            subject=subject,
            market=flag["market"],
            side=flag["side"],
            line=flag.get("market_line", flag.get("line")),
            edge_score=flag.get("edge_score"),
            price=flag.get("price"),
            opponent=flag.get("opponent"),
            home_team=flag.get("home_team"),
            away_team=flag.get("away_team"),
            commence_time=flag.get("commence_time"),
            explanation=flag.get("explanation"),
        )

    for flag in results.get("games", []):
        log_flag(flag, flag["market"], f"{flag['away_team']} @ {flag['home_team']}")
    for flag in results.get("props", []):
        log_flag(flag, "props_trend", flag["player"])
    for flag in results.get("props_speculative", []):
        log_flag(flag, "props_speculative", flag["player"])
    for flag in results.get("props_coverage", []):
        log_flag(flag, "props_coverage", flag["player"])
