import logging
import pandas as pd
from collections import Counter
from datetime import datetime

from screener.fetch_stats import get_weekly_player_stats, get_schedules
from screener.fetch_pbp import get_play_by_play
from screener.fetch_odds import get_events, get_game_odds, get_player_props, CFB_SPORT, NHL_SPORT, MLB_SPORT
from screener.fetch_cfb_stats import get_cfb_schedules, get_cfb_team_name_map, to_cfb_school_name
from screener.fetch_nhl_stats import get_nhl_schedule, get_nhl_goalie_game_logs
from screener.fetch_mlb_stats import get_mlb_schedule, get_pitcher_game_logs_for_schedule
from screener.nhl_team_map import to_nhl_abbr
from screener.nhl_goalies import resolve_starting_goalies, most_used_goalie
from screener.team_map import get_team_name_to_abbr, to_abbr
from screener.scoring import rank_props, rank_games
from model.power_ratings import compute_team_ratings, predict_matchup, screen_spread, screen_total
from model.cfb_power_ratings import compute_cfb_team_ratings, predict_cfb_matchup, screen_cfb_spread, screen_cfb_total
from model.nhl_power_ratings import (
    compute_nhl_team_ratings, compute_goalie_ratings, predict_nhl_matchup,
    screen_nhl_moneyline, screen_nhl_puckline, screen_nhl_total, PUCK_LINE,
)
from model.mlb_power_ratings import (
    compute_mlb_team_ratings, compute_park_factors, compute_pitcher_ratings, predict_mlb_matchup,
    screen_mlb_moneyline, screen_mlb_runline, screen_mlb_total, RUN_LINE,
)
from model.player_trends import (
    screen_player_prop, player_current_team, has_nfl_history,
    get_position_stat_ratings, player_adjusted_average,
)
from model.coverage_sim import (
    team_defensive_tendencies, player_coverage_splits, screen_simplified_coverage_prop, COVERAGE_MARKET_MAP,
)

logger = logging.getLogger(__name__)

# Bradley's call (2026-08-20): CFB totals have no backtested edge (same as the NFL total
# model) but aren't broken either — see model/cfb_power_ratings.py for the investigation.
# Kept live, routed to their own speculative section rather than the main results.
CFB_TOTALS_ENABLED = True


def consensus_price_and_point(quotes):
    """
    For a fixed-number spread market (MLB run line, NHL puck line), average a team's
    price only across bookmakers that agree on which side of the fixed line that team
    was actually quoted at. Either team can be the favorite (unlike NFL home-field,
    these sports' home edge is negligible-to-nonexistent), so a book listing a team's
    price under the opposite point value from another book isn't a typo -- it means the
    books disagree on who's favored, most often right before a near-even game. Averaging
    indiscriminately across both would silently mix a favorite's price with an
    underdog's, corrupting the edge calculation.

    quotes: list of (price, point) tuples for one team, one per bookmaker.
    Returns (avg_price, consensus_point), or (None, None) if quotes is empty.
    """
    if not quotes:
        return None, None
    consensus_point = Counter(point for _, point in quotes).most_common(1)[0][0]
    matching_prices = [price for price, point in quotes if point == consensus_point]
    return sum(matching_prices) / len(matching_prices), consensus_point


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


def get_cfb_current_week(cfb_schedules_df):
    """
    Find the nearest upcoming CFB week — same idea as get_current_week, but restricted to
    the most recent season in the data. cfbd's real-world data has occasional leftover
    "unplayed" rows from past seasons (e.g. a 2024 game postponed by a hurricane and never
    marked complete or rescheduled) — searching across all fetched years the way the NFL
    version does would get fooled by one of these into reporting a stale week from a season
    that's actually long over. Found in testing 2026-08-20 (App State @ Liberty, 2024 wk 5).
    """
    latest_season = cfb_schedules_df["season"].max()
    return get_current_week(cfb_schedules_df[cfb_schedules_df["season"] == latest_season])


def run_cfb_game_screener(cfb_schedules_df, cfb_name_map, current_season, current_week, markets="spreads,totals"):
    """
    Screen the upcoming week's FBS college football spread and total against our CFB
    power-rating model. Spread has a real backtested edge (see backtest/run_cfb_backtest.py);
    total does not, so its flags are kept separate as speculative rather than mixed into
    the main results — same treatment as the NFL total model.

    Games against an FCS opponent are silently skipped — predict_cfb_matchup returns None
    for them, since the FCS side never gets a real rating of its own.
    """
    ratings = compute_cfb_team_ratings(cfb_schedules_df)
    games = get_game_odds(markets=markets, sport=CFB_SPORT)
    flags = []
    speculative_flags = []

    for game in games:
        home_full, away_full = game["home_team"], game["away_team"]
        home_school = to_cfb_school_name(home_full, cfb_name_map)
        away_school = to_cfb_school_name(away_full, cfb_name_map)

        season, week = lookup_season_week(cfb_schedules_df, home_school, away_school)
        if current_week is not None and (season, week) != (current_season, current_week):
            continue  # only the upcoming week's slate, same reasoning as the NFL game screener

        prediction = predict_cfb_matchup(ratings, home_school, away_school)
        if prediction is None:
            logger.debug(f"Skipping {away_full} @ {home_full} — not enough CFB rating data yet, or an FCS opponent")
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
            "home_team": home_school,
            "away_team": away_school,
            "commence_time": game.get("commence_time"),
            "season": season,
            "week": week,
        }

        if spread_lines:
            flag = screen_cfb_spread(prediction, sum(spread_lines) / len(spread_lines))
            if flag:
                flag["price"] = sum(spread_prices) / len(spread_prices)
                flags.append({**flag, **game_context})

        if CFB_TOTALS_ENABLED and total_lines:
            flag = screen_cfb_total(prediction, sum(total_lines) / len(total_lines))
            if flag:
                flag["price"] = sum(total_prices) / len(total_prices)
                speculative_flags.append({**flag, **game_context})

    return rank_games(flags), rank_games(speculative_flags)


def get_cfb_stats_years():
    """Which CFB seasons to pull stats for — same reasoning as get_stats_years."""
    year = datetime.now().year
    return [year - 2, year - 1, year]


def get_nhl_current_season_year():
    """
    The NHL labels a season by its starting year (e.g. the 2024-25 season is '2024'),
    and the season runs roughly October through June. Before around August each year
    we're still in the previous season year even though the calendar year has ticked
    over — e.g. March 2027 is still the 2026 season.
    """
    now = datetime.now()
    return now.year if now.month >= 8 else now.year - 1


def get_nhl_stats_years():
    """Which NHL seasons to pull stats for — last 2 seasons plus the current one, same
    reasoning as get_stats_years."""
    current_season = get_nhl_current_season_year()
    return [current_season - 2, current_season - 1, current_season]


def run_nhl_game_screener(nhl_schedule_df, nhl_goalie_logs, games_window=25):
    """
    Screen today's NHL games (moneyline, puck line, total) against our goalie-adjusted
    power-rating model. Puck line is kept live but routed to its own speculative section
    — see model/nhl_power_ratings.py for why its backtest isn't trustworthy evidence of a
    real edge (a naive "always bet the road team" strategy wins the same market most of
    the time, a structural fact about hockey scoring, not a sign of model skill).

    Unlike NFL/CFB, there's no natural "week" for a sport that plays most days of the
    week — the ledger's "week" column is repurposed here to hold the game's calendar
    date as an integer (YYYYMMDD), which keeps the ledger's (season, week, subject,
    market) uniqueness meaningful for a day-based sport instead of a week-based one.

    Each goalie is resolved via the saves-prop confirmation signal where available
    (screener/nhl_goalies.py) and falls back to that team's own most-used goalie
    recently when not yet confirmed — every returned flag records whether both
    starters were actually confirmed, so the report can be honest about it.
    """
    team_ratings = compute_nhl_team_ratings(nhl_schedule_df, games_window)
    goalie_ratings = compute_goalie_ratings(nhl_goalie_logs, games_window)

    events = get_events(sport=NHL_SPORT)
    event_id_by_teams = {(e["home_team"], e["away_team"]): e["id"] for e in events}
    games = get_game_odds(markets="h2h,spreads,totals", sport=NHL_SPORT)

    flags = []
    speculative_flags = []

    for game in games:
        home_full, away_full = game["home_team"], game["away_team"]
        home_abbr, away_abbr = to_nhl_abbr(home_full), to_nhl_abbr(away_full)

        commence_time = game.get("commence_time")
        if not commence_time:
            continue
        game_date = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        season = game_date.year if game_date.month >= 8 else game_date.year - 1
        date_key = int(game_date.strftime("%Y%m%d"))

        event_id = event_id_by_teams.get((home_full, away_full))
        if event_id is not None:
            home_goalie_id, home_confirmed, away_goalie_id, away_confirmed = resolve_starting_goalies(
                event_id, home_abbr, away_abbr, goalie_ratings
            )
        else:
            logger.debug(f"No matching odds event for {away_full} @ {home_full} — using best-guess goalies")
            home_goalie_id = most_used_goalie(goalie_ratings, home_abbr)
            away_goalie_id = most_used_goalie(goalie_ratings, away_abbr)
            home_confirmed = away_confirmed = False

        prediction = predict_nhl_matchup(team_ratings, goalie_ratings, home_abbr, away_abbr, home_goalie_id, away_goalie_id)
        if prediction is None:
            logger.debug(f"Skipping {away_full} @ {home_full} — not enough NHL rating data yet")
            continue

        home_ml, away_ml, total_lines, total_prices = None, None, [], []
        home_puck_quotes, away_puck_quotes = [], []
        for bookmaker in game.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market["key"] == "h2h":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == home_full:
                            home_ml = outcome["price"]
                        elif outcome["name"] == away_full:
                            away_ml = outcome["price"]
                elif market["key"] == "spreads":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == home_full:
                            home_puck_quotes.append((outcome["price"], outcome["point"]))
                        elif outcome["name"] == away_full:
                            away_puck_quotes.append((outcome["price"], outcome["point"]))
                elif market["key"] == "totals":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == "Over":
                            total_lines.append(outcome["point"])
                            total_prices.append(outcome["price"])

        home_puck_odds, home_puck_point = consensus_price_and_point(home_puck_quotes)
        away_puck_odds, away_puck_point = consensus_price_and_point(away_puck_quotes)

        goalies_confirmed = home_confirmed and away_confirmed
        goalie_caveat = (
            "" if goalies_confirmed else
            " (starting goalies not yet confirmed — using each team's recent form as a placeholder.)"
        )
        game_context = {
            "home_team": home_abbr, "away_team": away_abbr, "commence_time": commence_time,
            "season": season, "week": date_key,
            "goalies_confirmed": goalies_confirmed,
        }

        if home_ml is not None and away_ml is not None:
            flag = screen_nhl_moneyline(prediction, home_ml, away_ml)
            if flag:
                flag["explanation"] += goalie_caveat
                flag["price"] = flag["market_odds"]
                flags.append({**flag, **game_context})

        if home_puck_odds is not None and away_puck_odds is not None:
            flag = screen_nhl_puckline(prediction, home_puck_odds, home_puck_point, away_puck_odds, away_puck_point)
            if flag:
                flag["explanation"] += goalie_caveat
                flag["price"] = flag["market_odds"]
                flag["market_line"] = PUCK_LINE
                speculative_flags.append({**flag, **game_context})

        if total_lines:
            flag = screen_nhl_total(prediction, sum(total_lines) / len(total_lines))
            if flag:
                flag["price"] = sum(total_prices) / len(total_prices)
                flag["explanation"] += goalie_caveat
                flags.append({**flag, **game_context})

    return rank_games(flags), rank_games(speculative_flags)


def run_nhl_screener():
    """Run the NHL portion of the screener on its own — see screener/nhl_schedule_gate.py
    for why this runs on its own dynamic schedule instead of the fixed 9am NFL/CFB time."""
    nhl_years = get_nhl_stats_years()
    nhl_schedule_df = get_nhl_schedule(nhl_years)
    nhl_goalie_logs = get_nhl_goalie_game_logs(nhl_years)
    return run_nhl_game_screener(nhl_schedule_df, nhl_goalie_logs)


def get_mlb_stats_years():
    """Which MLB seasons to pull stats for. The season runs within a single calendar
    year (unlike NHL/NFL, which straddle two), so last year plus the current one is
    enough to build ratings even very early in a new season."""
    year = datetime.now().year
    return [year - 1, year]


def run_mlb_game_screener(mlb_schedule_df, games_window=30, pitcher_games_window=8):
    """
    Screen today's/tomorrow's MLB games (moneyline, run line, total) against our
    pitcher- and park-adjusted power-rating model. Run line has a real backtested edge
    (see backtest/run_mlb_backtest.py); moneyline and total do not, so their flags are
    kept separate as speculative rather than mixed into the main results — same
    treatment the NFL/CFB total models get.

    Unlike NHL, MLB's own schedule API already publishes probable starting pitchers
    days ahead, so there's no goalie-style confirmation problem to work around — the
    schedule's own home_pitcher_id/away_pitcher_id is used directly.
    """
    team_ratings = compute_mlb_team_ratings(mlb_schedule_df, games_window)
    park_factors, _ = compute_park_factors(mlb_schedule_df)
    completed = mlb_schedule_df.dropna(subset=["home_score", "away_score"])
    pitcher_logs = get_pitcher_game_logs_for_schedule(completed)
    pitcher_ratings, _ = compute_pitcher_ratings(pitcher_logs, pitcher_games_window)

    upcoming = mlb_schedule_df[mlb_schedule_df["home_score"].isna()]
    schedule_by_matchup = {
        (row["home_team"], row["away_team"], row["game_date"]): row
        for _, row in upcoming.iterrows()
    }

    games = get_game_odds(markets="h2h,spreads,totals", sport=MLB_SPORT)
    flags = []
    speculative_flags = []

    for game in games:
        home_full, away_full = game["home_team"], game["away_team"]
        commence_time = game.get("commence_time")
        if not commence_time:
            continue
        game_date = datetime.fromisoformat(commence_time.replace("Z", "+00:00")).strftime("%Y-%m-%d")

        schedule_row = schedule_by_matchup.get((home_full, away_full, game_date))
        if schedule_row is None:
            logger.debug(f"No matching MLB schedule entry for {away_full} @ {home_full} on {game_date}")
            continue

        home_pitcher_id = schedule_row.get("home_pitcher_id")
        away_pitcher_id = schedule_row.get("away_pitcher_id")
        pitchers_confirmed = pd.notna(home_pitcher_id) and pd.notna(away_pitcher_id)

        prediction = predict_mlb_matchup(
            team_ratings, pitcher_ratings, park_factors, home_full, away_full,
            venue_name=schedule_row.get("venue_name"),
            home_pitcher_id=int(home_pitcher_id) if pd.notna(home_pitcher_id) else None,
            away_pitcher_id=int(away_pitcher_id) if pd.notna(away_pitcher_id) else None,
        )
        if prediction is None:
            logger.debug(f"Skipping {away_full} @ {home_full} — not enough MLB rating data yet")
            continue

        home_ml, away_ml, total_lines, total_prices = None, None, [], []
        home_rl_quotes, away_rl_quotes = [], []
        for bookmaker in game.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market["key"] == "h2h":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == home_full:
                            home_ml = outcome["price"]
                        elif outcome["name"] == away_full:
                            away_ml = outcome["price"]
                elif market["key"] == "spreads":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == home_full:
                            home_rl_quotes.append((outcome["price"], outcome["point"]))
                        elif outcome["name"] == away_full:
                            away_rl_quotes.append((outcome["price"], outcome["point"]))
                elif market["key"] == "totals":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == "Over":
                            total_lines.append(outcome["point"])
                            total_prices.append(outcome["price"])

        home_rl_odds, home_rl_point = consensus_price_and_point(home_rl_quotes)
        away_rl_odds, away_rl_point = consensus_price_and_point(away_rl_quotes)

        pitcher_caveat = (
            "" if pitchers_confirmed else
            " (probable starters not yet announced for this game — using each team's recent rotation form as a placeholder.)"
        )
        game_context = {
            "home_team": home_full, "away_team": away_full, "commence_time": commence_time,
            "season": schedule_row["season"], "week": int(game_date.replace("-", "")),
            "pitchers_confirmed": pitchers_confirmed,
        }

        if home_ml is not None and away_ml is not None:
            flag = screen_mlb_moneyline(prediction, home_ml, away_ml)
            if flag:
                flag["explanation"] += pitcher_caveat
                flag["price"] = flag["market_odds"]
                speculative_flags.append({**flag, **game_context})

        if home_rl_odds is not None and away_rl_odds is not None:
            flag = screen_mlb_runline(prediction, home_rl_odds, home_rl_point, away_rl_odds, away_rl_point)
            if flag:
                flag["explanation"] += pitcher_caveat
                flag["price"] = flag["market_odds"]
                flag["market_line"] = RUN_LINE
                flags.append({**flag, **game_context})

        if total_lines:
            flag = screen_mlb_total(prediction, sum(total_lines) / len(total_lines))
            if flag:
                flag["price"] = sum(total_prices) / len(total_prices)
                flag["explanation"] += pitcher_caveat
                speculative_flags.append({**flag, **game_context})

    return rank_games(flags), rank_games(speculative_flags)


def run_mlb_screener():
    """Run the MLB portion of the screener on its own entry point, mirroring the CFB/NHL
    pattern — but on the same fixed 9am schedule as NFL/CFB, since MLB's probable
    starters are announced well ahead of that time (unlike NHL's same-day goalie
    confirmations), so no dynamic scheduling is needed here."""
    mlb_years = get_mlb_stats_years()
    mlb_schedule_df = get_mlb_schedule(mlb_years)
    return run_mlb_game_screener(mlb_schedule_df)


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

    cfb_game_flags, cfb_totals_speculative, cfb_error = [], [], None
    if not props_only:
        try:
            cfb_years = get_cfb_stats_years()
            cfb_schedules_df = get_cfb_schedules(cfb_years)
            cfb_name_map = get_cfb_team_name_map(cfb_years[-1])
            cfb_current_season, cfb_current_week = get_cfb_current_week(cfb_schedules_df)
            if cfb_current_week is None:
                logger.warning("Could not determine the upcoming CFB week from the schedule — screening every game found instead of just the next slate")
            else:
                logger.info(f"Screening CFB for {cfb_current_season} week {cfb_current_week}")
            cfb_game_flags, cfb_totals_speculative = run_cfb_game_screener(
                cfb_schedules_df, cfb_name_map, cfb_current_season, cfb_current_week
            )
        except Exception as e:
            # A CFB-side failure (data source down, misconfigured key, etc.) should never
            # take down the whole NFL run — degrade to "no CFB picks this run" instead.
            # Recorded as cfb_error rather than just logged: a caught exception here is
            # invisible to Bradley otherwise, since the run still exits successfully and
            # sends a normal-looking email -- exactly what let CFB silently fail on every
            # scheduled run for two days in production before anyone noticed (2026-08-23).
            logger.error(f"CFB screening failed, skipping CFB for this run: {e}")
            cfb_error = str(e)

    mlb_flags, mlb_speculative, mlb_error = [], [], None
    if not props_only:
        try:
            mlb_flags, mlb_speculative = run_mlb_screener()
        except Exception as e:
            # Same isolation reasoning as the CFB block above -- an MLB-side failure
            # should never take down the NFL run, but must still be visible rather than
            # just logged (see log_results_to_ledger's cfb_error handling in main.py).
            logger.error(f"MLB screening failed, skipping MLB for this run: {e}")
            mlb_error = str(e)

    if games_only:
        prop_flags, prop_speculative, prop_no_data, prop_coverage = [], [], [], []
    else:
        prop_flags, prop_speculative, prop_no_data = run_props_screener(weekly_df, schedules_df, name_map, current_season, current_week)
        pbp_df = get_play_by_play(years)
        prop_coverage = run_coverage_screener(weekly_df, pbp_df, schedules_df, name_map, current_season, current_week)

    return {
        "games": game_flags,
        "cfb_games": cfb_game_flags,
        "cfb_totals_speculative": cfb_totals_speculative,
        "cfb_error": cfb_error,
        "mlb_games": mlb_flags,
        "mlb_speculative": mlb_speculative,
        "mlb_error": mlb_error,
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
            small_sample=flag.get("small_sample", False),
        )

    for flag in results.get("games", []):
        log_flag(flag, flag["market"], f"{flag['away_team']} @ {flag['home_team']}")
    for flag in results.get("cfb_games", []):
        log_flag(flag, f"cfb_{flag['market']}", f"{flag['away_team']} @ {flag['home_team']}")
    for flag in results.get("cfb_totals_speculative", []):
        log_flag(flag, f"cfb_{flag['market']}_speculative", f"{flag['away_team']} @ {flag['home_team']}")
    for flag in results.get("nhl_games", []):
        log_flag(flag, f"nhl_{flag['market']}", f"{flag['away_team']} @ {flag['home_team']}")
    for flag in results.get("nhl_puckline_speculative", []):
        log_flag(flag, f"nhl_{flag['market']}_speculative", f"{flag['away_team']} @ {flag['home_team']}")
    for flag in results.get("mlb_games", []):
        log_flag(flag, f"mlb_{flag['market']}", f"{flag['away_team']} @ {flag['home_team']}")
    for flag in results.get("mlb_speculative", []):
        log_flag(flag, f"mlb_{flag['market']}_speculative", f"{flag['away_team']} @ {flag['home_team']}")
    for flag in results.get("props", []):
        log_flag(flag, "props_trend", flag["player"])
    for flag in results.get("props_speculative", []):
        log_flag(flag, "props_speculative", flag["player"])
    for flag in results.get("props_coverage", []):
        log_flag(flag, "props_coverage", flag["player"])
