import logging
import math
import pandas as pd

from model.power_ratings import build_team_games, margin_to_win_probability

logger = logging.getLogger(__name__)

# Derived from real cfbd data, 2017-2024 regular season, FBS-vs-FBS games only (see
# backtest/simulate_cfb.py for the analysis) — not guessed. College home-field edge and
# score variance both run well above the NFL's (HOME_FIELD_ADVANTAGE=1.5,
# MARGIN_STD_DEV=13.5 in model/power_ratings.py), which matches the sport's reputation for
# noisier, more talent-driven blowouts.
HOME_FIELD_ADVANTAGE = 4.0  # points; real average FBS-vs-FBS home margin, 2017-2024
MARGIN_STD_DEV = 21.0  # points; real std dev of FBS-vs-FBS final margins, 2017-2024, used to turn a predicted margin into a win probability

# A team's FBS-vs-FCS games are still part of its own game log (an FBS team's performance
# in that game is real signal about their offense/defense), but the FCS opponent itself
# never gets its own rating — ratings_from_cfb_team_games explicitly filters to the real
# FBS team set for this reason. Without that filter, an FCS opponent would get its own
# rating computed off just its one game against an FBS team. Instead, an FCS opponent is
# only ever seen as an `opponent`, and treated as a fixed weak team below. Without any
# floor, the iterative rating math would treat any opponent it has no rating for as exactly
# league-average (see model.power_ratings.ratings_from_team_games's .fillna(0.0)) — which
# would badly understate the talent gap and make a rout of an FCS team look like a
# mediocre, unremarkable result instead of the near-guaranteed win it is.
# These two constants are a fixed floor used in place of that average, derived the same way
# the model derives every other team's rating (see the off_component/def_component math
# below) from the real, empirical scoring gap in FBS-vs-FCS games, 2017-2024: FBS teams
# average 43.5 points (vs. a 27.6 FBS-vs-FBS league average) and allow 14.1 points against
# FCS opponents.
FCS_OPPONENT_OFF_RATING = -13.5  # a fixed, weak stand-in "offense" for any unrated (FCS or below) opponent
FCS_OPPONENT_DEF_RATING = 16.0  # a fixed, weak stand-in "defense" for any unrated (FCS or below) opponent

RATING_ITERATIONS = 15  # same as the NFL model — how many passes the opponent-adjustment loop runs before settling
GAMES_WINDOW = 13  # a full FBS regular season is 12 games, plus a possible conference championship

# Calibrated against a walk-forward backtest of 2019-2024 (see backtest/run_cfb_backtest.py
# --sweep), same standard applied to the NFL model in model/power_ratings.py. Win rates are
# exact (real historical results); ROI is an estimate assuming standard -110 odds on every
# bet, since cfbd's historical lines don't include the actual spread/total price the way
# nfl_data_py's do.
#
# Two real bugs were found and fixed during backtesting (2026-08-20), both stemming from
# the same root cause: model.power_ratings.build_team_games creates a "team" row for BOTH
# sides of every game, so an FCS opponent (which should only ever be treated as a fixed,
# weak placeholder — see FCS_OPPONENT_OFF_RATING/DEF_RATING above) was silently getting its
# own real rating computed from just its one game against an FBS team, AND that bogus
# rating was then actually being used (via the opponent-lookup's dict match, bypassing the
# intended .fillna() fixed-floor fallback entirely) whenever an FBS team's own rating was
# adjusted for having played them. In live testing this produced absurd predictions (e.g.
# Northwestern favored by 50 over FCS South Dakota State). Fixed by explicitly filtering to
# the real FBS team set before any rating is computed (see ratings_from_cfb_team_games).
# Both bugs were invisible to the backtest's spread numbers only by coincidence — spread
# predictions cancel out a *uniform* rating offset, but this bug wasn't uniform; the
# corrected sweep below reflects the properly-fixed model, not the version described in
# earlier chat history.
#   - Spread: still a real, positive edge, just more modest than initially reported. Set to
#     4.0, which has both a strong sample size and a solidly positive result (2244 bets,
#     54.1% win rate, +3.3% ROI, +73.6 units) — not perfectly monotonic across the sweep
#     (edge>=8 dips to 52.1%/-0.5%, likely backtest noise given ~4,200 total games over 6
#     years), but the broad trend from edge>=2 through edge>=12 stays solidly profitable.
#   - Total: no proven edge (48.9% at threshold 6.0, -6.6% ROI), consistent both before and
#     after the fix — flat across the sweep, same shape as the NFL total model. Bradley's
#     call (2026-08-20): keep it live, same treatment as the NFL total model — visible, but
#     routed to its own "speculative" report section rather than the main list (see
#     screener/pipeline.py), not a recommendation. Threshold set at 6.0 (a bit above the
#     NFL total model's 4.5, given CFB's higher scoring variance) purely to keep the pick
#     volume reasonable — no threshold tested showed a meaningfully different win rate.
SPREAD_EDGE_THRESHOLD = 4.0
TOTAL_EDGE_THRESHOLD = 6.0

# Totals deep dive (2026-08-31, following the same investigation for NFL totals): Bradley
# noticed CFB predicted totals showing very wide swings and asked why. Found a real,
# serious reliability bug, now fixed in screener/fetch_cfb_stats.py: a transient cfbd API
# outage mid-fetch caused most years of a multi-year schedule/lines request to silently
# fail while one year succeeded, and since at least one year succeeded, the resulting
# PARTIAL dataset got cached as if it were the complete one. Every rating computed from
# that poisoned cache was then built from one frozen, years-stale season — e.g. Oklahoma
# State's home rating was identical to six decimal places across 34 games spanning
# 2019-2024, because its actual 2018-2024 games had silently vanished from the fetch and
# only its 2017 game log remained. This produced predicted totals as high as 95.7 and
# total edges up to 47.7 points. Fixed by refusing to cache a fetch where an
# already-completed season's year failed (see _reject_stale_year_failures) — a stale-year
# failure is now a hard, visible error instead of a silent, corrupted cache write.
# Re-verified with a clean cache: extreme (20+ point) edges dropped from 417 to 19 out of
# ~4,200 games, and the exact-duplicate-prediction pattern disappeared entirely. The
# underlying backtest conclusion is UNCHANGED by this fix (spread 2244/54.1%/+3.3%, total
# 1426/48.9%/-6.6% — identical to the pre-existing documented numbers above) — this was a
# fresh corruption from a live outage during this investigation, not a long-standing issue
# baked into those numbers.
#
# A smaller, real (not buggy) contributor also confirmed: recent FCS-to-FBS transition
# programs (James Madison, Sam Houston, Jacksonville State, Kennesaw State) genuinely have
# very little FBS-level rating history in their first 1-2 seasons (as few as 1-9 games vs.
# the normal 13), producing legitimately noisier predictions for their games — about 30 of
# 4,226 graded games (~0.7%). Not fixed — same unregularized-small-sample pattern the props
# model already handles with a scaled-up required edge (see model/player_trends.py), a
# reasonable future improvement if these thin-sample picks turn out to be a drag, but low
# volume enough not to prioritize yet.
#
# After excluding both of the above, CFB totals still show real, LEGITIMATE extra variance
# vs. NFL: predicted_total std dev 10.3 vs. NFL's ~6.6, and — importantly — the MARKET's own
# total_line std dev is also much wider for CFB (7.98) than NFL's (~4.46). This confirms
# the wider range itself is an accurate reflection of the sport (bigger talent gaps between
# a 130+ team field, transfer-portal roster churn between seasons, pace/style differences),
# not something to correct away. The no-edge conclusion for CFB totals stands as-is; the
# same spread-conviction co-filter that gave NFL totals a real edge (see
# model/power_ratings.py's TOTAL_SPREAD_CONVICTION_THRESHOLD) has not yet been tested here.


def ratings_from_cfb_team_games(team_games, fbs_teams, games_window=GAMES_WINDOW, iterations=RATING_ITERATIONS):
    """
    CFB version of model.power_ratings.ratings_from_team_games — same iterative
    opponent-adjustment math, but any opponent with no rating of its own (an FCS or
    lower-division team) is treated as a fixed weak team (FCS_OPPONENT_OFF_RATING/
    FCS_OPPONENT_DEF_RATING) instead of exactly league-average.

    `fbs_teams` (the real FBS team set) must be passed in explicitly and used to filter
    which rows get their own rating. Without this, model.power_ratings.build_team_games
    creates a "team" row for BOTH sides of every game — including the FCS side of an
    FBS-vs-FCS game — so an FCS team that played exactly one FBS opponent would get its own
    real rating computed off that single, wildly unrepresentative game (a real bug found in
    testing 2026-08-20: the model predicted Northwestern to beat FCS South Dakota State by
    50 points, because SDSU had picked up its own extreme one-game rating instead of being
    excluded). Filtering to fbs_teams here is what makes an FCS opponent correctly fall
    back to the fixed floor below, and keeps the mean-zero recentering from being skewed by
    these bogus one-game ratings.
    """
    team_games = team_games[team_games["team"].isin(fbs_teams)]
    recent = team_games.groupby("team").tail(games_window).copy()
    teams = recent["team"].unique()
    teams_set = set(teams)

    # league_avg_score is meant to represent true FBS-vs-FBS scoring level (what we're
    # actually predicting) — computed only from games against a real, rated opponent, so
    # FBS-vs-FCS blowout scores don't drag it upward.
    league_avg_score = recent.loc[recent["opponent"].isin(teams_set), "scored"].mean()

    off_rating = {t: 0.0 for t in teams}
    def_rating = {t: 0.0 for t in teams}

    for _ in range(iterations):
        opp_def = recent["opponent"].map(def_rating).fillna(FCS_OPPONENT_DEF_RATING)
        opp_off = recent["opponent"].map(off_rating).fillna(FCS_OPPONENT_OFF_RATING)
        off_component = recent["scored"] - league_avg_score - opp_def
        def_component = recent["allowed"] - league_avg_score - opp_off
        off_rating = off_component.groupby(recent["team"]).mean().to_dict()
        def_rating = def_component.groupby(recent["team"]).mean().to_dict()

    # Re-center both to mean zero across the FBS team set. The FCS floor above is a fixed,
    # non-reciprocal "opponent" — it never gets its own rating adjusted back, unlike a real
    # FBS opponent — so without this, the ~830 FBS-vs-FCS games each season quietly drag the
    # whole system's average off/def rating away from zero as the iteration re-balances
    # around them. That drift is invisible in spread predictions (a shared offset to every
    # team's rating cancels out in a head-to-head difference — confirmed against the
    # spread backtest before and after this fix, no change), but it directly inflates every
    # predicted total by about 2x the drift, since both teams' ratings feed into one sum.
    off_mean = sum(off_rating.values()) / len(off_rating)
    def_mean = sum(def_rating.values()) / len(def_rating)
    off_rating = {t: v - off_mean for t, v in off_rating.items()}
    def_rating = {t: v - def_mean for t, v in def_rating.items()}

    games_played = recent.groupby("team").size()
    ratings = pd.DataFrame({
        "team": list(off_rating.keys()),
        "off_rating": list(off_rating.values()),
        "def_rating": [def_rating[t] for t in off_rating.keys()],
    })
    ratings["league_avg_score"] = league_avg_score
    ratings["games"] = ratings["team"].map(games_played)
    return ratings


def compute_cfb_team_ratings(schedules_df, games_window=GAMES_WINDOW, iterations=RATING_ITERATIONS):
    """Build an opponent-adjusted power rating per FBS team from a full schedule."""
    fbs_teams = set(schedules_df.loc[schedules_df["home_classification"] == "fbs", "home_team"])
    team_games = build_team_games(schedules_df)
    return ratings_from_cfb_team_games(team_games, fbs_teams, games_window, iterations)


def predict_cfb_matchup(ratings_df, home_team, away_team):
    """
    Predict a score, spread, and total for one FBS matchup. Returns None if either team
    has no rating — this includes FCS opponents, which are filtered out of ratings_df
    (see ratings_from_cfb_team_games) rather than left with an unreliable one-game rating,
    so games against them aren't screened.
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
    home_win_prob = 0.5 * (1 + math.erf(predicted_spread / (MARGIN_STD_DEV * math.sqrt(2))))

    return {
        "home_team": home_team,
        "away_team": away_team,
        "predicted_home_score": round(predicted_home_score, 1),
        "predicted_away_score": round(predicted_away_score, 1),
        "predicted_spread": round(predicted_spread, 1),
        "predicted_total": round(predicted_total, 1),
        "home_win_prob": round(home_win_prob, 3),
    }


def screen_cfb_spread(prediction, market_spread_home, edge_threshold=SPREAD_EDGE_THRESHOLD):
    """Flag a CFB spread bet if our predicted margin disagrees with the market by enough to
    matter. `market_spread_home` follows standard convention: negative means the home team
    is favored — cfbd's own spread field already matches this, no sign flip needed."""
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


def screen_cfb_total(prediction, market_total, edge_threshold=TOTAL_EDGE_THRESHOLD):
    """Flag a CFB total (over/under) bet if our predicted total disagrees with the market by
    enough to matter."""
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
