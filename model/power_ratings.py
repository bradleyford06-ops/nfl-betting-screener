import logging
import math
import pandas as pd

logger = logging.getLogger(__name__)

HOME_FIELD_ADVANTAGE = 1.5  # points; rough long-run NFL average home-field edge
MARGIN_STD_DEV = 13.5  # typical standard deviation of NFL final score margins, used to turn a predicted margin into a win probability

# Thresholds are calibrated against a walk-forward backtest of 2019-2024 (see
# backtest/run_backtest.py), which is the real test — not just how much the model
# naturally disagrees with the market. A threshold sweep across that backtest found:
#   - Spread: a genuine, monotonic edge — bigger disagreements win more often
#     (50% at edge>=0 up to 56-60% at edge>=8-10). Set to 8.0, which balances a
#     reasonable sample size (156 bets, 56.4% win rate, +9.6% ROI) against edge strength.
#   - Total: NO signal at any threshold tested (0-8 points) — win rate stayed flat
#     around 49-50% and ROI stayed negative throughout. Kept enabled per Bradley's
#     explicit decision despite this (2026-08-15) — treat its picks as unproven/
#     exploratory, not evidence of real edge, until the total-prediction approach
#     itself is rethought.
#   - Moneyline: disabled entirely (see screener/pipeline.py) — even after the
#     opponent-adjustment fix, no threshold showed a clean, well-sampled edge.
SPREAD_EDGE_THRESHOLD = 8.0  # points of disagreement with the market before a spread is worth flagging
TOTAL_EDGE_THRESHOLD = 4.5  # points of disagreement before a total is worth flagging — NOT backed by backtest evidence of an edge
MONEYLINE_EDGE_THRESHOLD = 0.15  # unused live (moneyline screening is disabled) — kept for the backtest harness


RATING_ITERATIONS = 15  # how many passes the opponent-adjustment loop runs before settling


def build_team_games(schedules_df):
    """Reshape a schedule (one row per game) into one row per team per game, with
    that team's scored/allowed points and who they played — the raw material
    ratings are built from. Keeping the opponent is what lets ratings be adjusted
    for strength of schedule instead of just averaging raw scoring."""
    completed = schedules_df.dropna(subset=["home_score", "away_score"]).copy()
    completed = completed.sort_values(["season", "week"])

    home_rows = completed[["season", "week", "home_team", "away_team", "home_score", "away_score"]].rename(
        columns={"home_team": "team", "away_team": "opponent", "home_score": "scored", "away_score": "allowed"}
    )
    away_rows = completed[["season", "week", "away_team", "home_team", "away_score", "home_score"]].rename(
        columns={"away_team": "team", "home_team": "opponent", "away_score": "scored", "home_score": "allowed"}
    )
    return pd.concat([home_rows, away_rows]).sort_values(["team", "season", "week"])


def ratings_from_team_games(team_games, games_window=17, iterations=RATING_ITERATIONS):
    """
    Build opponent-adjusted offense/defense ratings from an already-reshaped team_games
    table (each team's last `games_window` games). This replaces plain scoring averages
    with an iterative fit: a team's offensive rating is how many points above/below
    league average they score *after* accounting for how tough each opponent's defense
    was, and vice versa for defense. Teams that padded their stats against weak
    opponents get corrected down; teams that performed well against strong opponents
    get corrected up. Split out from compute_team_ratings so a backtest can pass in only
    the games that happened *before* a given point in time.
    """
    recent = team_games.groupby("team").tail(games_window).copy()
    league_avg_score = recent["scored"].mean()

    teams = recent["team"].unique()
    off_rating = {t: 0.0 for t in teams}
    def_rating = {t: 0.0 for t in teams}

    for _ in range(iterations):
        opp_def = recent["opponent"].map(def_rating).fillna(0.0)
        opp_off = recent["opponent"].map(off_rating).fillna(0.0)
        off_component = recent["scored"] - league_avg_score - opp_def
        def_component = recent["allowed"] - league_avg_score - opp_off
        off_rating = off_component.groupby(recent["team"]).mean().to_dict()
        def_rating = def_component.groupby(recent["team"]).mean().to_dict()

    games_played = recent.groupby("team").size()
    ratings = pd.DataFrame({
        "team": list(off_rating.keys()),
        "off_rating": list(off_rating.values()),
        "def_rating": [def_rating[t] for t in off_rating.keys()],
    })
    ratings["league_avg_score"] = league_avg_score
    ratings["games"] = ratings["team"].map(games_played)
    return ratings


def compute_team_ratings(schedules_df, games_window=17, iterations=RATING_ITERATIONS):
    """
    Build an opponent-adjusted power rating per team from a full schedule. This is the
    foundation the model uses to predict spreads, totals, and moneylines.
    """
    team_games = build_team_games(schedules_df)
    return ratings_from_team_games(team_games, games_window, iterations)


def predict_matchup(ratings_df, home_team, away_team):
    """
    Predict a score, spread, and total for one matchup: each team's opponent-adjusted
    offensive rating against the other team's opponent-adjusted defensive rating.
    """
    home = ratings_df[ratings_df["team"] == home_team]
    away = ratings_df[ratings_df["team"] == away_team]
    if home.empty or away.empty:
        return None

    home = home.iloc[0]
    away = away.iloc[0]
    league_avg_score = home["league_avg_score"]

    predicted_home_score = league_avg_score + home["off_rating"] + away["def_rating"] + HOME_FIELD_ADVANTAGE / 2
    predicted_away_score = league_avg_score + away["off_rating"] + home["def_rating"] - HOME_FIELD_ADVANTAGE / 2

    predicted_spread = predicted_home_score - predicted_away_score  # positive = home favored
    predicted_total = predicted_home_score + predicted_away_score
    home_win_prob = margin_to_win_probability(predicted_spread)

    return {
        "home_team": home_team,
        "away_team": away_team,
        "predicted_home_score": round(predicted_home_score, 1),
        "predicted_away_score": round(predicted_away_score, 1),
        "predicted_spread": round(predicted_spread, 1),
        "predicted_total": round(predicted_total, 1),
        "home_win_prob": round(home_win_prob, 3),
    }


def margin_to_win_probability(margin, margin_std_dev=MARGIN_STD_DEV):
    """Convert a predicted point margin into a win probability using a normal-distribution
    approximation of how game margins are typically distributed. `margin_std_dev` defaults
    to the NFL's own value but can be overridden by other sports (e.g. the NHL model) whose
    game margins are distributed differently."""
    z = margin / (margin_std_dev * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


def american_odds_to_implied_prob(odds):
    """Convert American odds (e.g. -150, +130) into an implied win probability (includes vig)."""
    if odds > 0:
        return 100 / (odds + 100)
    return -odds / (-odds + 100)


def implied_prob_to_american_odds(prob):
    """Convert an implied win probability back into an equivalent American odds price —
    the inverse of american_odds_to_implied_prob, used to turn an averaged probability
    back into one representative price for display."""
    if prob >= 0.5:
        return -100 * prob / (1 - prob)
    return 100 * (1 - prob) / prob


def devig_two_way(prob_a, prob_b):
    """Remove the sportsbook's vig from a two-way market by normalizing implied probabilities to sum to 1."""
    total = prob_a + prob_b
    if total == 0:
        return prob_a, prob_b
    return prob_a / total, prob_b / total


def screen_spread(prediction, market_spread_home, edge_threshold=SPREAD_EDGE_THRESHOLD):
    """Flag a spread bet if our predicted margin disagrees with the market by enough to matter.
    `market_spread_home` follows standard convention: negative means the home team is favored."""
    market_home_margin = -market_spread_home
    edge = prediction["predicted_spread"] - market_home_margin
    if abs(edge) < edge_threshold:
        return None

    side = prediction["home_team"] if edge > 0 else prediction["away_team"]
    return {
        "market": "spread",
        "side": side,
        "market_line": round(market_spread_home, 1),
        "predicted_spread": prediction["predicted_spread"],
        "edge_score": round(abs(edge), 1),
        "explanation": (
            f"Model predicts {prediction['home_team']} wins by {prediction['predicted_spread']}, "
            f"vs. a market line implying a {market_home_margin:+.1f} home margin — "
            f"{abs(edge):.1f} points of disagreement favors {side}."
        ),
    }


def screen_total(prediction, market_total, edge_threshold=TOTAL_EDGE_THRESHOLD):
    """Flag a total (over/under) bet if our predicted total disagrees with the market by enough to matter."""
    edge = prediction["predicted_total"] - market_total
    if abs(edge) < edge_threshold:
        return None

    side = "Over" if edge > 0 else "Under"
    return {
        "market": "total",
        "side": side,
        "market_line": round(market_total, 1),
        "predicted_total": prediction["predicted_total"],
        "edge_score": round(abs(edge), 1),
        "explanation": (
            f"Model predicts a total of {prediction['predicted_total']} points "
            f"vs. a market total of {market_total:.1f} — {abs(edge):.1f} points of disagreement favors the {side.lower()}."
        ),
    }


def screen_moneyline(prediction, home_odds, away_odds, edge_threshold=MONEYLINE_EDGE_THRESHOLD):
    """Flag a moneyline bet if our model's win probability disagrees with the market's
    (vig-removed) implied probability by enough to matter."""
    home_implied = american_odds_to_implied_prob(home_odds)
    away_implied = american_odds_to_implied_prob(away_odds)
    home_fair, away_fair = devig_two_way(home_implied, away_implied)

    home_edge = prediction["home_win_prob"] - home_fair
    away_edge = -home_edge

    if abs(home_edge) < edge_threshold:
        return None

    if home_edge > 0:
        side, odds, edge = prediction["home_team"], home_odds, home_edge
        model_prob, market_prob = prediction["home_win_prob"], home_fair
    else:
        side, odds, edge = prediction["away_team"], away_odds, away_edge
        model_prob, market_prob = 1 - prediction["home_win_prob"], away_fair

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
