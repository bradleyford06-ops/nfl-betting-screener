import math
import logging
import pandas as pd

from model.power_ratings import american_odds_to_implied_prob, devig_two_way, canonical_team

logger = logging.getLogger(__name__)

# A separate win/loss-focused model built specifically for moneyline, after a 2026-08-30
# investigation found the scoring-margin power-rating model (model/power_ratings.py) has a
# structural gap for predicting who wins outright: it gets more confidently wrong, not less,
# as the market prices in things (backup QBs, situational tendencies) a pure box-score model
# can't see. This uses an Elo rating (as popularized for NFL by FiveThirtyEight) instead: a
# team's rating moves up after wins and down after losses, dampened by how surprising the
# result was, so it tracks a team's real win/loss quality directly rather than inferring it
# from scoring differential.

ELO_BASE = 1500.0  # starting rating for a team with no history
ELO_K = 20.0  # how fast ratings move after a single game — FiveThirtyEight's published NFL constant
HOME_FIELD_ELO = 48.0  # home-field advantage in Elo points — FiveThirtyEight's published NFL estimate
SEASON_REGRESSION = 1 / 3  # fraction of each team's rating pulled back toward league-average between seasons
MOV_DAMPENING_CONSTANT = 2.2  # tempers how much a blowout win moves ratings vs. a coin-flip win — FiveThirtyEight's published constant

# Team relocation handling (canonical_team, RELOCATION_MAP) lives in model/power_ratings.py
# and is imported above — kept in one place since model/power_ratings.py picked up the same
# need on 2026-08-31 (see its own comment for the story of how that surfaced).

# Backtested against real 2014-2024 moneylines (1999-2013 burn-in, see
# backtest/run_elo_backtest.py): at this threshold, 345 bets, 41.4% win rate, +3.6% ROI.
# Shipped live despite the sub-50% win rate — Bradley's explicit call (2026-08-30) after a
# root-cause investigation (see CLAUDE.md) found this model is a modest accuracy
# improvement over the older scoring-margin model but still behind the market, and its
# picks get LESS reliable, not more, at the biggest disagreements (win rate falls toward
# 0-20% past edge~0.34, thin samples). Treat this as the most speculative signal in the
# whole project, not a proven edge like NFL spread or MLB run line.
ELO_MONEYLINE_EDGE_THRESHOLD = 0.15


def elo_win_probability(elo_a, elo_b):
    """Standard Elo win-probability formula: how often the side rated `elo_a` beats one rated `elo_b`."""
    return 1.0 / (10 ** (-(elo_a - elo_b) / 400) + 1)


def margin_of_victory_multiplier(margin, elo_diff_of_winner):
    """
    Scale how much a result moves ratings by how decisive it was, tempered so that a blowout
    between two already-mismatched teams doesn't move ratings as much as the same margin
    between two evenly-matched teams (which is a much bigger upset). FiveThirtyEight's published
    NFL formula. A tie (margin=0, rare in the NFL) is treated as the smallest possible margin
    for this calculation, but still updates ratings based on how surprising a tie itself was.
    """
    effective_margin = max(abs(margin), 1)
    return math.log(effective_margin + 1) * (MOV_DAMPENING_CONSTANT / (elo_diff_of_winner * 0.001 + MOV_DAMPENING_CONSTANT))


def regress_to_mean(elo, mean=ELO_BASE, factor=SEASON_REGRESSION):
    """Pull a team's rating partway back toward league-average between seasons — an offseason
    (trades, draft, coaching changes) means last year's rating shouldn't carry forward at full strength."""
    return elo + (mean - elo) * factor


def build_elo_history(schedules_df, qb_adjustments=None):
    """
    Walk through every completed game in chronological order exactly once, predicting each
    game from ratings built only from earlier games (Elo updates incrementally, so this single
    forward pass is inherently free of lookahead — no need to re-fit ratings per game the way
    the scoring-margin power-rating model's backtest does).

    `qb_adjustments`, if given, is a dict of {(canonical_team, season, week): elo_points} — a
    prediction-time-only adjustment layered on top of a team's persistent rating (see
    model/qb_adjustment.py) to account for that week's starting QB being better or worse than
    what the team has recently gotten at the position. It does not feed back into the
    persistent team rating itself; the team rating still updates only from the actual result.

    Returns a DataFrame with one row per game carrying its pre-game ratings, model win
    probability, and the original schedule columns (so a backtest can grade it against real
    market lines) — plus the final team ratings dict for use in a live prediction.
    """
    completed = schedules_df.dropna(subset=["home_score", "away_score"]).copy()
    completed = completed.sort_values(["season", "week"]).reset_index(drop=True)

    team_elo = {}
    current_season = None
    rows = []

    for game in completed.itertuples():
        season, week = game.season, game.week
        if current_season is not None and season != current_season:
            team_elo = {team: regress_to_mean(elo) for team, elo in team_elo.items()}
        current_season = season

        home = canonical_team(game.home_team)
        away = canonical_team(game.away_team)
        home_elo = team_elo.get(home, ELO_BASE)
        away_elo = team_elo.get(away, ELO_BASE)

        home_qb_adj = qb_adjustments.get((home, season, week), 0.0) if qb_adjustments else 0.0
        away_qb_adj = qb_adjustments.get((away, season, week), 0.0) if qb_adjustments else 0.0

        effective_home_elo = home_elo + home_qb_adj + HOME_FIELD_ELO
        effective_away_elo = away_elo + away_qb_adj

        home_win_prob = elo_win_probability(effective_home_elo, effective_away_elo)

        row = game._asdict()
        row.update({
            "home_team_canonical": home,
            "away_team_canonical": away,
            "home_elo_pregame": round(home_elo, 1),
            "away_elo_pregame": round(away_elo, 1),
            "home_qb_adjustment": round(home_qb_adj, 1),
            "away_qb_adjustment": round(away_qb_adj, 1),
            "home_win_prob": home_win_prob,
        })
        rows.append(row)

        margin = game.home_score - game.away_score
        actual_home_result = 1.0 if margin > 0 else (0.0 if margin < 0 else 0.5)
        elo_diff_of_winner = (effective_home_elo - effective_away_elo) if margin >= 0 else (effective_away_elo - effective_home_elo)
        multiplier = margin_of_victory_multiplier(margin, elo_diff_of_winner)
        shift = ELO_K * multiplier * (actual_home_result - home_win_prob)

        team_elo[home] = home_elo + shift
        team_elo[away] = away_elo - shift

    history_df = pd.DataFrame(rows).drop(columns=["Index"], errors="ignore")
    return history_df, team_elo


def predict_matchup(team_elo, home_team, away_team, home_qb_adjustment=0.0, away_qb_adjustment=0.0):
    """Predict a live matchup's home win probability from current ratings — used by the
    screener, separate from build_elo_history's backtest/training path."""
    home = canonical_team(home_team)
    away = canonical_team(away_team)
    home_elo = team_elo.get(home, ELO_BASE)
    away_elo = team_elo.get(away, ELO_BASE)

    effective_home_elo = home_elo + home_qb_adjustment + HOME_FIELD_ELO
    effective_away_elo = away_elo + away_qb_adjustment

    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_elo": round(home_elo, 1),
        "away_elo": round(away_elo, 1),
        "home_win_prob": round(elo_win_probability(effective_home_elo, effective_away_elo), 3),
    }


def screen_elo_moneyline(prediction, home_odds, away_odds, edge_threshold=ELO_MONEYLINE_EDGE_THRESHOLD):
    """
    Flag a moneyline bet if this Elo model's win probability disagrees with the market's
    (vig-removed) implied probability by enough to matter. Speculative (see
    ELO_MONEYLINE_EDGE_THRESHOLD above) — kept live per Bradley's explicit choice despite
    the backtest not clearing the same bar as this project's proven markets.
    """
    home_implied = american_odds_to_implied_prob(home_odds)
    away_implied = american_odds_to_implied_prob(away_odds)
    home_fair, away_fair = devig_two_way(home_implied, away_implied)

    home_edge = prediction["home_win_prob"] - home_fair
    if abs(home_edge) < edge_threshold:
        return None

    if home_edge > 0:
        side, odds, edge = prediction["home_team"], home_odds, home_edge
        model_prob, market_prob = prediction["home_win_prob"], home_fair
    else:
        side, odds, edge = prediction["away_team"], away_odds, -home_edge
        model_prob, market_prob = 1 - prediction["home_win_prob"], away_fair

    return {
        "market": "moneyline",
        "side": side,
        "market_odds": odds,
        "model_win_prob": round(model_prob, 3),
        "market_implied_prob": round(market_prob, 3),
        "edge_score": round(edge, 3),
        "explanation": (
            f"Elo model (win/loss-based, not scoring margin) gives {side} a {model_prob*100:.0f}% "
            f"win probability vs. a market-implied {market_prob*100:.0f}% — a {edge*100:.1f} point "
            f"edge at {odds} odds. Speculative: this signal hasn't beaten the market's own accuracy "
            f"in backtesting — see the dashboard note before trusting it like spread or run line picks."
        ),
    }
