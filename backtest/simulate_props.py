import logging
import pandas as pd

from screener.fetch_stats import get_weekly_player_stats, build_position_stat_team_games
from model.power_ratings import ratings_from_team_games
from model.player_trends import TEAM_RATING_GAMES_WINDOW, TEAM_RATING_ITERATIONS

logger = logging.getLogger(__name__)

RECENT_GAMES_WINDOW = 8  # matches the live model's player-trend window
LONG_RUN_GAMES_WINDOW = 17  # how far back the synthetic "line" baseline looks
MIN_LONG_RUN_GAMES = 8  # don't compute a synthetic line off fewer than this many prior games


def load_props_backtest_data(years):
    """Fetch weekly player stats for the backtest window. No real prop-line data exists
    for free, so this backtest can only validate the signal against a player's own
    trailing average, not a real market line — see run_props_backtest.py for that caveat."""
    return get_weekly_player_stats(years)


def compute_synthetic_bets(weekly_df, position, stat_column):
    """
    Walk forward through every player's career at this position: at each game, use only
    the games strictly before it to build a synthetic "line" (the player's own trailing
    long-run average), a recent-trend signal (opponent-adjusted, same as the live model),
    and a defense signal for that week's actual opponent. Records what actually happened
    so we can check afterward whether the signals predicted the right direction.
    """
    position_df = weekly_df[weekly_df["position"] == position].sort_values(["season", "week"])
    if position_df.empty:
        return pd.DataFrame()

    team_games = build_position_stat_team_games(weekly_df, position, stat_column)

    # Precompute opponent-adjusted ratings once per (season, week) cutoff, reused across
    # every player at this position — this is the expensive step, so don't repeat it per player.
    cutoffs = sorted(set(zip(position_df["season"], position_df["week"])))
    ratings_by_cutoff = {}
    for season, week in cutoffs:
        prior_team_games = team_games[
            (team_games["season"] < season) | ((team_games["season"] == season) & (team_games["week"] < week))
        ]
        ratings_by_cutoff[(season, week)] = ratings_from_team_games(
            prior_team_games, TEAM_RATING_GAMES_WINDOW, TEAM_RATING_ITERATIONS
        )

    results = []
    for player_name, player_games in position_df.groupby("player_display_name"):
        player_games = player_games.sort_values(["season", "week"]).reset_index(drop=True)

        for i in range(len(player_games)):
            row = player_games.iloc[i]
            prior_games = player_games.iloc[:i]
            if len(prior_games) < MIN_LONG_RUN_GAMES:
                continue

            long_run_window = prior_games.tail(LONG_RUN_GAMES_WINDOW)
            synthetic_line = long_run_window[stat_column].mean()
            if not synthetic_line:
                continue

            ratings = ratings_by_cutoff.get((row["season"], row["week"]))
            if ratings is None or ratings.empty:
                continue
            def_rating_by_team = ratings.set_index("team")["def_rating"]

            recent_window = prior_games.tail(RECENT_GAMES_WINDOW)
            opponent_adjustment = recent_window["opponent_team"].map(def_rating_by_team).fillna(0.0)
            player_trend = (recent_window[stat_column] - opponent_adjustment).mean()

            opponent_row = ratings[ratings["team"] == row["opponent_team"]]
            if opponent_row.empty:
                continue
            league_avg = opponent_row["league_avg_score"].iloc[0]
            if not league_avg:
                continue
            defense_adjusted_allowed = league_avg + opponent_row["def_rating"].iloc[0]

            results.append({
                "player": player_name,
                "position": position,
                "stat": stat_column,
                "season": row["season"],
                "week": row["week"],
                "synthetic_line": synthetic_line,
                "player_trend": player_trend,
                "player_edge_pct": (player_trend - synthetic_line) / synthetic_line,
                "defense_edge_pct": (defense_adjusted_allowed - league_avg) / league_avg,
                "actual_value": row[stat_column],
                "actual_beat_line": row[stat_column] > synthetic_line,
            })

    return pd.DataFrame(results)


def measure_volatility(results_df):
    """
    How much a player's actual game-to-game output naturally swings around their own
    trailing average — the raw noise level a threshold has to clear to mean anything.
    Reported as a percentage of the synthetic line so different stats are comparable.
    """
    relative_deviation = (results_df["actual_value"] - results_df["synthetic_line"]) / results_df["synthetic_line"]
    return {
        "n": len(results_df),
        "stdev_pct": round(relative_deviation.std() * 100, 1),
        "mean_pct": round(relative_deviation.mean() * 100, 1),
    }


def sweep_thresholds(results_df, player_thresholds, defense_thresholds, min_bets=20):
    """
    For each combination of player-edge / defense-edge thresholds, replay what would have
    been flagged (same same-direction gate the live model uses) and check the hit rate —
    did the actual result land on the side the signals predicted, relative to the
    synthetic line.
    """
    same_direction = (
        ((results_df["player_edge_pct"] > 0) & (results_df["defense_edge_pct"] > 0))
        | ((results_df["player_edge_pct"] < 0) & (results_df["defense_edge_pct"] < 0))
    )

    rows = []
    for player_threshold in player_thresholds:
        for defense_threshold in defense_thresholds:
            passes = (
                same_direction
                & (results_df["player_edge_pct"].abs() >= player_threshold)
                & (results_df["defense_edge_pct"].abs() >= defense_threshold)
            )
            flagged = results_df[passes]
            if len(flagged) < min_bets:
                continue

            predicted_over = flagged["player_edge_pct"] > 0
            correct = (predicted_over & flagged["actual_beat_line"]) | (~predicted_over & ~flagged["actual_beat_line"])
            rows.append({
                "player_threshold": player_threshold,
                "defense_threshold": defense_threshold,
                "n": len(flagged),
                "hit_rate": round(correct.mean(), 3),
            })

    return pd.DataFrame(rows)
