import logging
import pandas as pd

from model.power_ratings import (
    build_team_games, ratings_from_team_games, margin_to_win_probability,
    american_odds_to_implied_prob, devig_two_way,
)

logger = logging.getLogger(__name__)

# Derived from real NHL scores, 2019/2021-2023 regular seasons (2020's covid-shortened
# bubble season excluded as unrepresentative) — see backtest/simulate_nhl.py.
HOME_ICE_ADVANTAGE = 0.22  # goals; NHL home-ice edge is much smaller than NFL's home-field edge
MARGIN_STD_DEV = 2.6  # goals; typical standard deviation of NHL final score margins

RATING_ITERATIONS = 15
GAMES_WINDOW = 25  # roughly the same season-fraction as the NFL model's 17-game window, scaled to an 82-game season

PUCK_LINE = 1.5  # the NHL puck line is essentially always fixed at +/-1.5 goals; books move the odds, not the number

# Calibrated against a walk-forward backtest of the 2021-22 and 2022-23 seasons — the
# only two seasons with real, correctly-attributed historical odds in the free data
# source available (see backtest/simulate_nhl.py, backtest/run_nhl_backtest.py --sweep).
#   - Moneyline (real historical prices — trustworthy): a genuine, if noisy, edge.
#     43.5% win rate/+9.7% ROI with no filter at all, up to 76%/+45.7% at edge>=0.25 but
#     on a thin, decaying sample (only 86 bets and a win-rate collapse by edge>=0.30).
#     Set to 0.05 — the best combination of sample size (1840 bets) and real ROI (+11.0%,
#     the highest total profit of any threshold tested). Win rate well under 50% is
#     expected and not a red flag: this targets undervalued underdogs by design.
#   - Total: a real, fairly consistent edge (unlike the NFL/CFB total models, which
#     found none) — ROI climbs from -2.9% unfiltered to +4.1%/+4.4% at edge 1.0-1.5
#     before the sample gets too thin to trust past that. Set to 1.0 (620 bets, 54.5%
#     win rate, +4.1% ROI, the best total profit among reasonably-sized samples).
#   - Puck line: NOT validated by this backtest, and the raw number is actively
#     misleading — a naive "always bet the road team +1.5" strategy with zero model
#     wins 67.6% of *all* 2,624 games in the dataset, a structural fact about hockey
#     (most games are decided by one goal) that real sportsbooks already price into the
#     puck line. The free data has no real per-game puck-line price, only an assumed
#     flat rate, so the backtest's apparent 65-79% win rate mostly reflects that
#     structural pattern, not real model skill. Kept live at Bradley's explicit choice
#     (2026-08-22), same precedent as NFL/CFB totals, but labeled speculative/unproven
#     in the report — do not treat its win rate/ROI as evidence of a real edge.
MONEYLINE_EDGE_THRESHOLD = 0.05
PUCKLINE_EDGE_THRESHOLD = 0.20
TOTAL_EDGE_THRESHOLD = 1.0


def compute_nhl_team_ratings(schedules_df, games_window=GAMES_WINDOW, iterations=RATING_ITERATIONS):
    """
    Build an opponent-adjusted goals-for/against power rating per team from a full NHL
    schedule, using the same iterative math as the NFL/CFB models. This is the team-level
    baseline the goalie layer then adjusts per matchup.
    """
    team_games = build_team_games(schedules_df.assign(week=schedules_df["game_date"]))
    return ratings_from_team_games(team_games, games_window, iterations)


def compute_goalie_ratings(goalie_game_logs, games_window=GAMES_WINDOW):
    """
    Build a goals-saved-above-expected (GSAx) rating per goalie: how many fewer goals a
    goalie has allowed than the shot quality they faced would predict, per game, over
    their last `games_window` appearances. Uses MoneyPuck's own xGoals model (which
    already accounts for shot danger) as the expectation, so this isolates goaltending
    skill from the team's shot-suppression in front of them — no separate opponent
    adjustment needed the way the team rating requires one.
    """
    recent = goalie_game_logs.sort_values(["playerId", "gameDate"]).groupby("playerId").tail(games_window)
    recent = recent.assign(goals_saved_above_expected=recent["xGoals"] - recent["goals"])

    ratings = (
        recent.groupby(["playerId", "playerTeam"])
        .agg(
            name=("name", "last"),
            games=("gameId", "count"),
            gsax_per_game=("goals_saved_above_expected", "mean"),
        )
        .reset_index()
    )
    return ratings


def team_average_goalie_effect(goalie_ratings, team):
    """
    A team's own historical average goalie effect, weighted by games played — this is
    what the team's overall defense rating already implicitly assumes about goaltending.
    Used so a specific starter's rating only contributes the *difference* from that
    team's usual goaltending, instead of double-counting the part the team rating
    already captures.
    """
    team_goalies = goalie_ratings[goalie_ratings["playerTeam"] == team]
    if team_goalies.empty or team_goalies["games"].sum() == 0:
        return 0.0
    return (team_goalies["gsax_per_game"] * team_goalies["games"]).sum() / team_goalies["games"].sum()


def predict_nhl_matchup(team_ratings, goalie_ratings, home_team, away_team, home_goalie_id=None, away_goalie_id=None):
    """
    Predict a score, puck-line cover probability, and total for one matchup: each team's
    opponent-adjusted offense/defense rating, adjusted by how the specific starting
    goalies compare to each team's own historical average goaltending. If a starting
    goalie isn't known/confirmed yet, the team's own average goaltending is used instead
    (equivalent to no adjustment) rather than guessing.
    """
    home = team_ratings[team_ratings["team"] == home_team]
    away = team_ratings[team_ratings["team"] == away_team]
    if home.empty or away.empty:
        return None

    home, away = home.iloc[0], away.iloc[0]
    league_avg_score = home["league_avg_score"]

    def goalie_adjustment(goalie_id, team):
        team_avg = team_average_goalie_effect(goalie_ratings, team)
        if goalie_id is None:
            return 0.0
        goalie_row = goalie_ratings[goalie_ratings["playerId"] == goalie_id]
        if goalie_row.empty:
            return 0.0
        return goalie_row.iloc[0]["gsax_per_game"] - team_avg

    home_goalie_adj = goalie_adjustment(home_goalie_id, home_team)
    away_goalie_adj = goalie_adjustment(away_goalie_id, away_team)

    predicted_home_score = (
        league_avg_score + home["off_rating"] + away["def_rating"] + HOME_ICE_ADVANTAGE / 2 - away_goalie_adj
    )
    predicted_away_score = (
        league_avg_score + away["off_rating"] + home["def_rating"] - HOME_ICE_ADVANTAGE / 2 - home_goalie_adj
    )
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
        "home_goalie_id": home_goalie_id,
        "away_goalie_id": away_goalie_id,
    }


def screen_nhl_moneyline(prediction, home_odds, away_odds):
    """Flag a moneyline bet if our model's win probability disagrees with the market's
    (vig-removed) implied probability by enough to matter. Same logic as the NFL model."""
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


def screen_nhl_puckline(prediction, home_puck_line_odds, away_puck_line_odds):
    """
    Flag a puck-line bet (the fixed +/-1.5 goal spread) if our model's probability of the
    favorite covering disagrees with the market's vig-removed implied probability. Unlike
    a moving NFL spread, the NHL puck-line number itself almost never changes — only the
    odds do — so this is graded like a moneyline (compare probabilities) rather than like
    the NFL's screen_spread (compare predicted margins to a market number).
    """
    home_cover_prob = margin_to_win_probability(prediction["predicted_margin"] - PUCK_LINE, margin_std_dev=MARGIN_STD_DEV)

    home_implied = american_odds_to_implied_prob(home_puck_line_odds)
    away_implied = american_odds_to_implied_prob(away_puck_line_odds)
    home_fair, away_fair = devig_two_way(home_implied, away_implied)

    home_edge = home_cover_prob - home_fair
    if abs(home_edge) < PUCKLINE_EDGE_THRESHOLD:
        return None

    if home_edge > 0:
        side, odds, model_prob, market_prob, edge = f"{prediction['home_team']} -{PUCK_LINE}", home_puck_line_odds, home_cover_prob, home_fair, home_edge
    else:
        side, odds, model_prob, market_prob, edge = f"{prediction['away_team']} +{PUCK_LINE}", away_puck_line_odds, 1 - home_cover_prob, away_fair, -home_edge

    return {
        "market": "puckline",
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


def screen_nhl_total(prediction, market_total):
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
            f"Model predicts a total of {prediction['predicted_total']} goals "
            f"vs. a market total of {market_total:.1f} — {abs(edge):.1f} goals of disagreement favors the {side.lower()}."
        ),
    }
