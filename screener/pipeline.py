import logging
from datetime import datetime

from screener.fetch_stats import get_weekly_player_stats, get_schedules
from screener.fetch_odds import get_events, get_game_odds, get_player_props
from screener.team_map import get_team_name_to_abbr, to_abbr
from screener.scoring import rank_props, rank_games
from model.power_ratings import compute_team_ratings, predict_matchup, screen_spread, screen_total
from model.player_trends import screen_player_prop, player_current_team, has_nfl_history

logger = logging.getLogger(__name__)


def get_stats_years():
    """Which seasons to pull stats for. Requests the last 3 years — any year whose data
    hasn't been published yet (including the current season before it starts) is
    automatically skipped by the fetch layer, so this always falls back to real data."""
    year = datetime.now().year
    return [year - 2, year - 1, year]


def run_game_screener(schedules_df, name_map, markets="spreads,totals"):
    """
    Screen every upcoming NFL game's spread and total against our power-rating model.

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

        prediction = predict_matchup(ratings, home_abbr, away_abbr)
        if prediction is None:
            logger.debug(f"Skipping {away_full} @ {home_full} — not enough rating data yet")
            continue

        spread_lines, total_lines = [], []
        for bookmaker in game.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market["key"] == "spreads":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == home_full:
                            spread_lines.append(outcome["point"])
                elif market["key"] == "totals":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == "Over":
                            total_lines.append(outcome["point"])

        game_context = {
            "home_team": home_abbr,
            "away_team": away_abbr,
            "commence_time": game.get("commence_time"),
        }

        if spread_lines:
            flag = screen_spread(prediction, sum(spread_lines) / len(spread_lines))
            if flag:
                flags.append({**flag, **game_context})

        if total_lines:
            flag = screen_total(prediction, sum(total_lines) / len(total_lines))
            if flag:
                flags.append({**flag, **game_context})

    return rank_games(flags)


def run_props_screener(weekly_df, name_map, games_window=8):
    """
    Screen every available player prop line against the player-vs-defense trend model.

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

        try:
            props = get_player_props(event["id"])
        except Exception as e:
            logger.warning(f"Failed to fetch props for {away_full} @ {home_full}: {e}")
            continue

        # Average each player's line across bookmakers before screening, same as game odds
        lines_by_player_market = {}
        for bookmaker in props.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                market_key = market["key"]
                for outcome in market["outcomes"]:
                    if outcome["name"] != "Over":
                        continue
                    player_name = outcome["description"]
                    key = (market_key, player_name)
                    lines_by_player_market.setdefault(key, []).append(outcome["point"])

        for (market_key, player_name), lines in lines_by_player_market.items():
            avg_line = sum(lines) / len(lines)

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
                (speculative_flags if flag["speculative"] else flags).append(flag)

    return rank_props(flags), rank_props(speculative_flags), no_data


def run_screener(props_only=False, games_only=False):
    """Run the full NFL betting screener: fetch data, run both models, return ranked results."""
    years = get_stats_years()
    weekly_df = get_weekly_player_stats(years)
    schedules_df = get_schedules(years)
    name_map = get_team_name_to_abbr()

    game_flags = [] if props_only else run_game_screener(schedules_df, name_map)
    prop_flags, prop_speculative, prop_no_data = ([], [], []) if games_only else run_props_screener(weekly_df, name_map)

    return {
        "games": game_flags,
        "props": prop_flags,
        "props_speculative": prop_speculative,
        "props_no_data": prop_no_data,
    }
