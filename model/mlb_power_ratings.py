import logging
import pandas as pd

from model.power_ratings import (
    build_team_games, ratings_from_team_games, margin_to_win_probability,
    american_odds_to_implied_prob, devig_two_way,
)

logger = logging.getLogger(__name__)

# Derived from real MLB scores, 2023-2026 regular seasons (9,252 games).
HOME_FIELD_ADVANTAGE = 0.02  # runs; essentially negligible in MLB, unlike the other sports here -- a real, well-known finding, not a bug
MARGIN_STD_DEV = 4.53  # runs; typical standard deviation of MLB final score margins

RATING_ITERATIONS = 15
GAMES_WINDOW = 30  # trailing games for team rating -- proportionally similar to the other models' fraction of a full season
PITCHER_GAMES_WINDOW = 8  # trailing starts for a pitcher rating -- roughly 6 weeks at a normal 5-day rotation

RUN_LINE = 1.5  # the MLB run line is essentially always fixed at +/-1.5 runs; books move the odds, not the number

# Calibrated against a walk-forward backtest of the 2018-2021 seasons (8,187 games) --
# the coverage of the free historical odds source (see backtest/simulate_mlb.py,
# backtest/run_mlb_backtest.py --sweep). A single-season (2018-only) test run looked
# promising for moneyline (51.7% win rate, +14.6% ROI at edge>=0.30) but that turned out
# to be noise -- the full 4-season sample reversed it completely.
#   - Run line: the strongest, cleanest real edge found across every sport in this
#     project so far. 55.4% win rate/+10.6% ROI with no filter at all, climbing cleanly
#     to 59.6%/+25.5% at edge>=0.30 -- verified not a mechanical "always bet the dog"
#     artifact (a healthy 64/36 side split) and, unlike the NHL puck-line result, this
#     ROI is measured against real market prices (~79% of the time; ~21% falls back to
#     an assumed standard price where the free data has no real one), not a flat
#     assumption -- so a profit here is real evidence, not a pricing artifact. Set to
#     0.10 (5,897 bets, 56.5% win rate, +13.9% ROI, within 5% of the single best total
#     profit found at any threshold tested).
#   - Moneyline: no edge across the full sample -- negative ROI at every threshold,
#     barely breaking even (-0.1%) even at the strictest one tested. Kept live at
#     Bradley's explicit choice (2026-08-24), labeled speculative, same treatment as
#     the NFL/CFB total models -- do not treat its picks as evidence of real edge.
#   - Total: also no edge (flat ~50%, negative ROI throughout) -- same pattern as the
#     NFL/CFB total models. Kept live, labeled speculative, per the same precedent
#     Bradley has chosen every other time this project's backtest found nothing here.
MONEYLINE_EDGE_THRESHOLD = 0.20
RUNLINE_EDGE_THRESHOLD = 0.10
TOTAL_EDGE_THRESHOLD = 1.0


def compute_mlb_team_ratings(schedule_df, games_window=GAMES_WINDOW, iterations=RATING_ITERATIONS):
    """Opponent-adjusted runs-for/against rating per team, same iterative math as the
    NFL/CFB/NHL models."""
    team_games = build_team_games(schedule_df.assign(week=schedule_df["game_date"]))
    return ratings_from_team_games(team_games, games_window, iterations)


def compute_park_factors(schedule_df):
    """
    Each park's scoring environment relative to league average, computed directly from
    real completed games rather than a third-party source: average total runs in games
    played AT that park, divided by the league-wide average. >1.0 means a hitter's park
    (e.g. Coors Field), <1.0 a pitcher's park. A simplified version of the standard
    approach -- doesn't control for which specific teams played there -- but every team
    plays roughly the same home/away split, so this washes out reasonably well.
    """
    completed = schedule_df.dropna(subset=["home_score", "away_score"]).copy()
    completed["total_runs"] = completed["home_score"] + completed["away_score"]
    league_avg_total = completed["total_runs"].mean()
    park_avg = completed.groupby("venue_name")["total_runs"].mean()
    return (park_avg / league_avg_total).to_dict(), league_avg_total


def compute_pitcher_ratings(pitcher_logs, games_window=PITCHER_GAMES_WINDOW):
    """
    Each starting pitcher's earned-runs-per-9-innings over their last `games_window`
    starts, compared to the league average over the same window. Positive `rating` means
    the pitcher allows fewer earned runs than average (better).
    """
    recent = pitcher_logs.sort_values(["pitcher_id", "date"]).groupby("pitcher_id").tail(games_window)
    recent = recent[recent["innings_pitched"] > 0]
    if recent.empty:
        return pd.DataFrame(columns=["pitcher_id", "team", "era", "starts", "rating"]), 4.30

    league_avg_era = recent["earned_runs"].sum() / recent["innings_pitched"].sum() * 9

    ratings = recent.groupby(["pitcher_id", "team"]).agg(
        earned_runs=("earned_runs", "sum"),
        innings_pitched=("innings_pitched", "sum"),
        starts=("game_pk", "count"),
    ).reset_index()
    ratings["era"] = ratings["earned_runs"] / ratings["innings_pitched"] * 9
    ratings["rating"] = league_avg_era - ratings["era"]
    return ratings, league_avg_era


def team_average_pitcher_effect(pitcher_ratings, team):
    """
    A team's own average starting-pitcher effect recently, weighted by starts -- what
    the team's overall rating already implicitly assumes about its rotation. Used so a
    specific starter's rating only contributes the *difference* from that team's usual
    pitching, instead of double-counting what the team rating already captures (same
    reasoning as the NHL model's team_average_goalie_effect).
    """
    team_pitchers = pitcher_ratings[pitcher_ratings["team"] == team]
    if team_pitchers.empty or team_pitchers["starts"].sum() == 0:
        return 0.0
    return (team_pitchers["rating"] * team_pitchers["starts"]).sum() / team_pitchers["starts"].sum()


def predict_mlb_matchup(team_ratings, pitcher_ratings, park_factors, home_team, away_team,
                         venue_name=None, home_pitcher_id=None, away_pitcher_id=None):
    """
    Predict a score, run-line cover probability, and total for one matchup: each team's
    opponent-adjusted offense/defense rating, adjusted by how the specific starting
    pitchers compare to each team's own recent rotation average, then scaled by that
    park's real scoring environment. If a starting pitcher isn't announced yet, the
    team's own average rotation effect is used instead (no adjustment) rather than
    guessing -- MLB officially publishes probable starters days ahead, so this should be
    the exception rather than the rule by the time a game is actually screened.
    """
    home = team_ratings[team_ratings["team"] == home_team]
    away = team_ratings[team_ratings["team"] == away_team]
    if home.empty or away.empty:
        return None

    home, away = home.iloc[0], away.iloc[0]
    league_avg_score = home["league_avg_score"]

    def pitcher_adjustment(pitcher_id, team):
        team_avg = team_average_pitcher_effect(pitcher_ratings, team)
        if pitcher_id is None:
            return 0.0
        row = pitcher_ratings[(pitcher_ratings["pitcher_id"] == pitcher_id) & (pitcher_ratings["team"] == team)]
        if row.empty:
            return 0.0
        return row.iloc[0]["rating"] - team_avg

    home_pitcher_adj = pitcher_adjustment(home_pitcher_id, home_team)
    away_pitcher_adj = pitcher_adjustment(away_pitcher_id, away_team)
    park_factor = park_factors.get(venue_name, 1.0)

    predicted_home_score = (league_avg_score + home["off_rating"] + away["def_rating"] + HOME_FIELD_ADVANTAGE / 2 - away_pitcher_adj) * park_factor
    predicted_away_score = (league_avg_score + away["off_rating"] + home["def_rating"] - HOME_FIELD_ADVANTAGE / 2 - home_pitcher_adj) * park_factor
    predicted_home_score = max(predicted_home_score, 0.5)
    predicted_away_score = max(predicted_away_score, 0.5)

    predicted_margin = predicted_home_score - predicted_away_score
    predicted_total = predicted_home_score + predicted_away_score
    home_win_prob = margin_to_win_probability(predicted_margin, margin_std_dev=MARGIN_STD_DEV)

    return {
        "home_team": home_team,
        "away_team": away_team,
        "predicted_home_score": round(predicted_home_score, 2),
        "predicted_away_score": round(predicted_away_score, 2),
        "predicted_margin": round(predicted_margin, 2),
        "predicted_total": round(predicted_total, 2),
        "home_win_prob": round(home_win_prob, 3),
        "home_pitcher_id": home_pitcher_id,
        "away_pitcher_id": away_pitcher_id,
        "park_factor": round(park_factor, 3),
    }


def screen_mlb_moneyline(prediction, home_odds, away_odds):
    """Flag a moneyline bet if our model's win probability disagrees with the market's
    (vig-removed) implied probability by enough to matter. Same logic as the NFL/NHL models."""
    home_implied = american_odds_to_implied_prob(home_odds)
    away_implied = american_odds_to_implied_prob(away_odds)
    home_fair, away_fair = devig_two_way(home_implied, away_implied)

    home_edge = prediction["home_win_prob"] - home_fair
    if abs(home_edge) < MONEYLINE_EDGE_THRESHOLD:
        return None

    if home_edge > 0:
        side, odds, model_prob, market_prob, edge = prediction["home_team"], home_odds, prediction["home_win_prob"], home_fair, home_edge
    else:
        side, odds, model_prob, market_prob, edge = prediction["away_team"], away_odds, 1 - prediction["home_win_prob"], away_fair, -home_edge

    return {
        "market": "moneyline",
        "side": side,
        "market_odds": odds,
        "model_win_prob": round(model_prob, 3),
        "market_implied_prob": round(market_prob, 3),
        "edge_score": round(abs(edge), 3),
        "explanation": (
            f"Model gives {side} a {model_prob*100:.0f}% win probability vs. a "
            f"market-implied {market_prob*100:.0f}% — a {abs(edge)*100:.1f} point edge at {odds} odds."
        ),
    }


def screen_mlb_runline(prediction, home_runline_odds, away_runline_odds):
    """
    Flag a run-line bet (the fixed +/-1.5 run spread) if our model's probability of the
    favorite covering disagrees with the market's vig-removed implied probability. Same
    reasoning as the NHL model's puck-line screening -- the number itself almost never
    moves, only the odds do, so this is graded like a moneyline rather than like a
    moving NFL spread.
    """
    home_cover_prob = margin_to_win_probability(prediction["predicted_margin"] - RUN_LINE, margin_std_dev=MARGIN_STD_DEV)

    home_implied = american_odds_to_implied_prob(home_runline_odds)
    away_implied = american_odds_to_implied_prob(away_runline_odds)
    home_fair, away_fair = devig_two_way(home_implied, away_implied)

    home_edge = home_cover_prob - home_fair
    if abs(home_edge) < RUNLINE_EDGE_THRESHOLD:
        return None

    if home_edge > 0:
        side, odds, model_prob, market_prob, edge = f"{prediction['home_team']} -{RUN_LINE}", home_runline_odds, home_cover_prob, home_fair, home_edge
    else:
        side, odds, model_prob, market_prob, edge = f"{prediction['away_team']} +{RUN_LINE}", away_runline_odds, 1 - home_cover_prob, away_fair, -home_edge

    return {
        "market": "runline",
        "side": side,
        "market_odds": odds,
        "model_cover_prob": round(model_prob, 3),
        "market_implied_prob": round(market_prob, 3),
        "edge_score": round(abs(edge), 3),
        "explanation": (
            f"Model gives {side} a {model_prob*100:.0f}% cover probability vs. a "
            f"market-implied {market_prob*100:.0f}% — a {abs(edge)*100:.1f} point edge at {odds} odds."
        ),
    }


def screen_mlb_total(prediction, market_total):
    """Flag a total (over/under) bet if our predicted total disagrees with the market by enough to matter."""
    edge = prediction["predicted_total"] - market_total
    if abs(edge) < TOTAL_EDGE_THRESHOLD:
        return None

    side = "Over" if edge > 0 else "Under"
    return {
        "market": "total",
        "side": side,
        "market_line": round(market_total, 1),
        "predicted_total": prediction["predicted_total"],
        "edge_score": round(abs(edge), 2),
        "explanation": (
            f"Model predicts a total of {prediction['predicted_total']} runs "
            f"vs. a market total of {market_total:.1f} — {abs(edge):.1f} runs of disagreement favors the {side.lower()}."
        ),
    }
