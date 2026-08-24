"""
Shared bet-sizing math, used by the edge-sizing backtest analysis
(backtest/analyze_edge_sizing.py) and, once validated, by the dashboard's suggested-
stake display. Kept in one place so every sport uses the same formula rather than each
model inventing its own sizing logic.

Every other part of this project stakes flat 1 unit per bet regardless of edge size.
This module exists to test — and, if the evidence supports it, to size — bets in
proportion to how strong the edge actually is.
"""

import math


def decimal_payout(american_odds):
    """The 'b' term of the Kelly formula: net profit per 1 unit staked at these odds."""
    if american_odds > 0:
        return american_odds / 100
    return 100 / abs(american_odds)


def kelly_fraction(model_prob, american_odds):
    """The Kelly-optimal fraction of bankroll to stake, given the model's own win/cover
    probability and the price being offered. Zero (never negative) means no edge at
    these odds — the formula would say 'don't bet'."""
    b = decimal_payout(american_odds)
    q = 1 - model_prob
    f = (b * model_prob - q) / b
    return max(f, 0.0)


def suggested_stake_units(model_prob, american_odds, threshold_kelly_fraction,
                            kelly_multiplier=0.25, min_units=1.0, max_units=3.0):
    """
    Translate a bet's Kelly fraction into a stake on the flat-1-unit scale this project
    already uses everywhere else (every backtest, the live ledger, every ROI number
    reported so far). A bet with exactly today's live edge threshold's Kelly fraction
    sizes to 1 unit — today's flat-staking convention is the floor, not a change —
    stronger edges scale up proportionally, capped so one extreme edge can't imply an
    unreasonable stake.

    kelly_multiplier applies fractional (default quarter) Kelly rather than full Kelly —
    the standard real-world safety margin against model error and variance; full Kelly
    is well known to be too aggressive to actually use.

    threshold_kelly_fraction is the Kelly fraction (already scaled by kelly_multiplier)
    of a bet sitting exactly at today's live edge threshold for this market — the
    normalization anchor for what counts as "1 unit."
    """
    f = kelly_fraction(model_prob, american_odds) * kelly_multiplier
    if threshold_kelly_fraction <= 0:
        return min_units
    units = f / threshold_kelly_fraction
    return max(min_units, min(units, max_units))


def model_prob_from_points_edge(edge_score, margin_std_dev):
    """
    For points-edge markets (NFL/CFB spread, NHL/MLB total) there's no separately
    modeled win probability in this project — spread/total bets aren't framed
    probabilistically anywhere else in the codebase. Reuses the existing
    margin_to_win_probability curve (model/power_ratings.py), treating the edge itself
    as a margin advantage over a fair (~50/50) line — the same math already used
    elsewhere in this project to turn a predicted margin into a probability, just
    applied to the edge instead of the raw prediction.
    """
    z = edge_score / (margin_std_dev * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


def model_prob_from_ledger_row(edge_score, price):
    """
    For probability-edge markets (moneyline, run line, puck line), approximate the
    model's own win/cover probability from only what the permanent ledger actually
    stores for a pick: its own price (the market's implied probability, with that
    side's vig still in it) plus the stored edge_score (model_prob - fair market_prob).
    This is an approximation — the ledger doesn't separately store the model's raw
    probability — used only where the real model_prob isn't available (i.e. the
    dashboard, working from historical ledger rows). The backtest analysis itself uses
    the real stored model_prob directly instead of this approximation.
    """
    from model.power_ratings import american_odds_to_implied_prob
    market_implied = american_odds_to_implied_prob(price)
    return min(1.0, max(0.0, market_implied + edge_score))


# MLB run line is the only market backtest/analyze_edge_sizing.py found real evidence
# for (see that script's 2026-08-23 run): win rate climbs fairly cleanly from ~51% to
# ~60% as edge grows, and Kelly-scaled staking beat flat staking on 5,897 real bets
# (+16.2% ROI vs. +13.9% flat) — see model/mlb_power_ratings.py for the run-line model
# itself. Every other market tested (NFL/CFB spread, NHL moneyline/total) showed a
# noisy or even backwards relationship between edge size and outcome quality — sizing
# up on those would be adding false precision, not real information, so they stay flat.
MLB_RUNLINE_KELLY_MULTIPLIER = 0.25  # quarter-Kelly, the standard safety margin
MLB_RUNLINE_ANCHOR_ODDS = -110  # standard juice, used only to normalize "1 unit" — see suggested_stake_units


def mlb_runline_stake_units(edge_score, price):
    """Suggested stake (in the project's usual flat-1-unit units) for a real MLB run-line
    pick, using only fields already stored on a ledger row — no schema change needed.
    The only market this project currently has real backtested evidence for sizing on."""
    from model.mlb_power_ratings import RUNLINE_EDGE_THRESHOLD

    model_prob = model_prob_from_ledger_row(edge_score, price)
    # Anchor computed the same way real bets are (implied prob at the anchor odds, plus
    # the threshold edge) so "1 unit" lines up with a real threshold-edge bet, not a
    # theoretical fair-market one.
    threshold_prob = model_prob_from_ledger_row(RUNLINE_EDGE_THRESHOLD, MLB_RUNLINE_ANCHOR_ODDS)
    threshold_kelly = kelly_fraction(threshold_prob, MLB_RUNLINE_ANCHOR_ODDS) * MLB_RUNLINE_KELLY_MULTIPLIER
    return round(suggested_stake_units(model_prob, price, threshold_kelly, kelly_multiplier=MLB_RUNLINE_KELLY_MULTIPLIER), 2)
