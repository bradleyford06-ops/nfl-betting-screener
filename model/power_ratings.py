import logging
import math
import pandas as pd

logger = logging.getLogger(__name__)

HOME_FIELD_ADVANTAGE = 1.5  # points; rough long-run NFL average home-field edge
MARGIN_STD_DEV = 13.5  # typical standard deviation of NFL final score margins, used to turn a predicted margin into a win probability

# These thresholds are calibrated against how much this model naturally disagrees with the
# market on ANY given game, even when it's not actually right (measured empirically: spread
# disagreement has a ~3.0 point stdev, total ~2.3 points, moneyline probability ~0.09).
# Thresholds are set at roughly 1.5-2x that noise band so only the more unusual disagreements
# get flagged — this reduces false positives but does NOT mean flagged bets are proven correct;
# only a backtest against actual game outcomes can show whether the model has real skill.
SPREAD_EDGE_THRESHOLD = 5.0  # points of disagreement with the market before a spread is worth flagging
TOTAL_EDGE_THRESHOLD = 4.5  # points of disagreement before a total is worth flagging
MONEYLINE_EDGE_THRESHOLD = 0.15  # probability-point disagreement before a moneyline is worth flagging


def build_team_games(schedules_df):
    """Reshape a schedule (one row per game) into one row per team per game, with
    that team's scored/allowed points — the raw material ratings are built from."""
    completed = schedules_df.dropna(subset=["home_score", "away_score"]).copy()
    completed = completed.sort_values(["season", "week"])

    home_rows = completed[["season", "week", "home_team", "home_score", "away_score"]].rename(
        columns={"home_team": "team", "home_score": "scored", "away_score": "allowed"}
    )
    away_rows = completed[["season", "week", "away_team", "away_score", "home_score"]].rename(
        columns={"away_team": "team", "away_score": "scored", "home_score": "allowed"}
    )
    return pd.concat([home_rows, away_rows]).sort_values(["team", "season", "week"])


def ratings_from_team_games(team_games, games_window=17):
    """Average each team's last `games_window` games (from an already-reshaped team_games
    table) into a power rating. Split out from compute_team_ratings so a backtest can pass
    in only the games that happened *before* a given point in time."""
    recent = team_games.groupby("team").tail(games_window)
    return (
        recent.groupby("team")
        .agg(avg_scored=("scored", "mean"), avg_allowed=("allowed", "mean"), games=("scored", "count"))
        .reset_index()
    )


def compute_team_ratings(schedules_df, games_window=17):
    """
    Build a simple power rating per team: average points scored and average points
    allowed over each team's last `games_window` completed games. This is the
    foundation the model uses to predict spreads, totals, and moneylines.
    """
    team_games = build_team_games(schedules_df)
    return ratings_from_team_games(team_games, games_window)


def predict_matchup(ratings_df, home_team, away_team):
    """
    Predict a score, spread, and total for one matchup by blending each team's
    scoring average with the opponent's average points allowed.
    """
    home = ratings_df[ratings_df["team"] == home_team]
    away = ratings_df[ratings_df["team"] == away_team]
    if home.empty or away.empty:
        return None

    home = home.iloc[0]
    away = away.iloc[0]

    predicted_home_score = (home["avg_scored"] + away["avg_allowed"]) / 2 + HOME_FIELD_ADVANTAGE / 2
    predicted_away_score = (away["avg_scored"] + home["avg_allowed"]) / 2 - HOME_FIELD_ADVANTAGE / 2

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


def margin_to_win_probability(margin):
    """Convert a predicted point margin into a win probability using a normal-distribution
    approximation of how NFL game margins are typically distributed."""
    z = margin / (MARGIN_STD_DEV * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


def american_odds_to_implied_prob(odds):
    """Convert American odds (e.g. -150, +130) into an implied win probability (includes vig)."""
    if odds > 0:
        return 100 / (odds + 100)
    return -odds / (-odds + 100)


def devig_two_way(prob_a, prob_b):
    """Remove the sportsbook's vig from a two-way market by normalizing implied probabilities to sum to 1."""
    total = prob_a + prob_b
    if total == 0:
        return prob_a, prob_b
    return prob_a / total, prob_b / total


def screen_spread(prediction, market_spread_home):
    """Flag a spread bet if our predicted margin disagrees with the market by enough to matter.
    `market_spread_home` follows standard convention: negative means the home team is favored."""
    market_home_margin = -market_spread_home
    edge = prediction["predicted_spread"] - market_home_margin
    if abs(edge) < SPREAD_EDGE_THRESHOLD:
        return None

    side = prediction["home_team"] if edge > 0 else prediction["away_team"]
    return {
        "market": "spread",
        "side": side,
        "market_line": market_spread_home,
        "predicted_spread": prediction["predicted_spread"],
        "edge_score": round(abs(edge), 1),
        "explanation": (
            f"Model predicts {prediction['home_team']} wins by {prediction['predicted_spread']}, "
            f"vs. a market line implying a {market_home_margin:+.1f} home margin — "
            f"{abs(edge):.1f} points of disagreement favors {side}."
        ),
    }


def screen_total(prediction, market_total):
    """Flag a total (over/under) bet if our predicted total disagrees with the market by enough to matter."""
    edge = prediction["predicted_total"] - market_total
    if abs(edge) < TOTAL_EDGE_THRESHOLD:
        return None

    side = "Over" if edge > 0 else "Under"
    return {
        "market": "total",
        "side": side,
        "market_line": market_total,
        "predicted_total": prediction["predicted_total"],
        "edge_score": round(abs(edge), 1),
        "explanation": (
            f"Model predicts a total of {prediction['predicted_total']} points "
            f"vs. a market total of {market_total} — {abs(edge):.1f} points of disagreement favors the {side.lower()}."
        ),
    }


def screen_moneyline(prediction, home_odds, away_odds):
    """Flag a moneyline bet if our model's win probability disagrees with the market's
    (vig-removed) implied probability by enough to matter."""
    home_implied = american_odds_to_implied_prob(home_odds)
    away_implied = american_odds_to_implied_prob(away_odds)
    home_fair, away_fair = devig_two_way(home_implied, away_implied)

    home_edge = prediction["home_win_prob"] - home_fair
    away_edge = -home_edge

    if abs(home_edge) < MONEYLINE_EDGE_THRESHOLD:
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
