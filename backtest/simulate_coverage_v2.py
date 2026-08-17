import logging
import pandas as pd

from screener.fetch_stats import build_position_stat_team_games
from model.power_ratings import ratings_from_team_games
from model.player_trends import TEAM_RATING_GAMES_WINDOW, TEAM_RATING_ITERATIONS
from model.coverage_sim import team_defensive_tendencies, PLAYER_COVERAGE_GAMES_WINDOW

logger = logging.getLogger(__name__)

RECENT_GAMES_WINDOW = 8  # matches the trend model's player-volume window
LONG_RUN_GAMES_WINDOW = 17  # synthetic-line baseline, same as the other backtests
MIN_LONG_RUN_GAMES = 8
MIN_COVERAGE_TARGETS = 5


def compute_synthetic_simplified_bets(weekly_df, pbp_df, position, stat_column):
    """
    Walk-forward backtest for the simplified coverage model: player volume trend (opponent-
    adjusted targets, same machinery as the trend model) x a zone/man efficiency modifier,
    compared against the player's own trailing long-run average of `stat_column`
    ('receptions' or 'receiving_yards') — the same synthetic-line methodology used
    throughout, so results are directly comparable to the trend model and the full
    (rejected) coverage simulation.

    Both team-defense tendencies AND the target-volume ratings are precomputed once per
    (season, week) cutoff and reused across every player that week — recomputing either
    per player-week, rather than per cutoff, is what made the first coverage backtest
    pathologically slow.
    """
    position_df = weekly_df[weekly_df["position"] == position].sort_values(["season", "week"]).reset_index(drop=True)
    team_games_targets = build_position_stat_team_games(weekly_df, position, "targets")
    pass_plays = pbp_df[pbp_df["pass"] == 1]

    cutoffs = sorted(set(zip(position_df["season"], position_df["week"])))
    def_by_cutoff, target_ratings_by_cutoff = {}, {}
    for season, week in cutoffs:
        prior_pbp = pbp_df[(pbp_df["season"] < season) | ((pbp_df["season"] == season) & (pbp_df["week"] < week))]
        def_by_cutoff[(season, week)] = team_defensive_tendencies(prior_pbp, TEAM_RATING_GAMES_WINDOW)

        prior_team_games = team_games_targets[
            (team_games_targets["season"] < season) | ((team_games_targets["season"] == season) & (team_games_targets["week"] < week))
        ]
        target_ratings_by_cutoff[(season, week)] = ratings_from_team_games(
            prior_team_games, TEAM_RATING_GAMES_WINDOW, TEAM_RATING_ITERATIONS
        )

    results = []
    for player_id, player_games in position_df.groupby("player_id"):
        player_games = player_games.sort_values(["season", "week"]).reset_index(drop=True)
        player_name = player_games["player_display_name"].iloc[0]
        player_pbp_targets = pass_plays[pass_plays["receiver_player_id"] == player_id]

        for i in range(len(player_games)):
            row = player_games.iloc[i]
            season, week = row["season"], row["week"]

            prior_games = player_games.iloc[:i]
            if len(prior_games) < MIN_LONG_RUN_GAMES:
                continue

            long_run = prior_games.tail(LONG_RUN_GAMES_WINDOW)
            synthetic_line = long_run[stat_column].mean()
            if not synthetic_line:
                continue

            ratings = target_ratings_by_cutoff.get((season, week))
            if ratings is None or ratings.empty:
                continue
            def_rating_by_team = ratings.set_index("team")["def_rating"]
            recent = prior_games.tail(RECENT_GAMES_WINDOW)
            opponent_adjustment = recent["opponent_team"].map(def_rating_by_team).fillna(0.0)
            player_avg_targets = (recent["targets"] - opponent_adjustment).mean()
            if pd.isna(player_avg_targets) or player_avg_targets <= 0:
                continue

            def_tendencies = def_by_cutoff.get((season, week))
            if def_tendencies is None or def_tendencies.empty:
                continue
            opponent_row = def_tendencies[def_tendencies["team"] == row["opponent_team"]]
            if opponent_row.empty:
                continue
            zone_rate = opponent_row["avg_zone_rate"].iloc[0]
            if pd.isna(zone_rate):
                continue

            prior_pbp_targets = player_pbp_targets[
                (player_pbp_targets["season"] < season) | ((player_pbp_targets["season"] == season) & (player_pbp_targets["week"] < week))
            ]
            played_games = prior_pbp_targets[["season", "week"]].drop_duplicates().sort_values(["season", "week"]).tail(PLAYER_COVERAGE_GAMES_WINDOW)
            coverage_targets = prior_pbp_targets.merge(played_games, on=["season", "week"])
            coverage_targets = coverage_targets[coverage_targets["defense_man_zone_type"].notna()]
            if len(coverage_targets) < MIN_COVERAGE_TARGETS:
                continue

            splits = {}
            for label, value in [("zone", "ZONE_COVERAGE"), ("man", "MAN_COVERAGE")]:
                subset = coverage_targets[coverage_targets["defense_man_zone_type"] == value]
                n = len(subset)
                if n == 0:
                    splits[label] = {"targets": 0, "catch_rate": 0, "yards_per_target": 0}
                    continue
                catches = subset["complete_pass"].sum()
                yards = subset["receiving_yards"].fillna(0).sum()
                splits[label] = {"targets": n, "catch_rate": catches / n, "yards_per_target": yards / n}

            blended_catch_rate = zone_rate * splits["zone"]["catch_rate"] + (1 - zone_rate) * splits["man"]["catch_rate"]
            blended_ypt = zone_rate * splits["zone"]["yards_per_target"] + (1 - zone_rate) * splits["man"]["yards_per_target"]
            predicted_value = player_avg_targets * (blended_catch_rate if stat_column == "receptions" else blended_ypt)

            results.append({
                "player": player_name,
                "season": season,
                "week": week,
                "synthetic_line": synthetic_line,
                "predicted_value": predicted_value,
                "edge_pct": (predicted_value - synthetic_line) / synthetic_line,
                "min_sample": min(splits["zone"]["targets"], splits["man"]["targets"]),
                "actual_value": row[stat_column],
                "actual_beat_line": row[stat_column] > synthetic_line,
            })

    return pd.DataFrame(results)


def sweep_thresholds(results_df, thresholds, min_bets=20):
    """Same sweep methodology as the other backtests: hit rate at each required-edge level."""
    rows = []
    for threshold in thresholds:
        flagged = results_df[results_df["edge_pct"].abs() >= threshold]
        if len(flagged) < min_bets:
            continue
        predicted_over = flagged["edge_pct"] > 0
        correct = (predicted_over & flagged["actual_beat_line"]) | (~predicted_over & ~flagged["actual_beat_line"])
        rows.append({"threshold": threshold, "n": len(flagged), "hit_rate": round(correct.mean(), 3)})
    return pd.DataFrame(rows)
