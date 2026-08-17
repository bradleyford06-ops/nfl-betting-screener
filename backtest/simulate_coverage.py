import logging
import pandas as pd

from screener.fetch_pbp import get_play_by_play
from model.coverage_sim import (
    team_offensive_tendencies,
    team_defensive_tendencies,
    simulate_matchup_passing,
    TEAM_TENDENCY_GAMES_WINDOW,
    PLAYER_COVERAGE_GAMES_WINDOW,
)

logger = logging.getLogger(__name__)

LONG_RUN_GAMES_WINDOW = 17  # same synthetic-line baseline used in the trend-model backtest
MIN_LONG_RUN_GAMES = 8
MIN_COVERAGE_TARGETS = 5  # don't even attempt a prediction below this many charted targets total


def load_coverage_backtest_data(years):
    """Fetch play-by-play data for the backtest window."""
    return get_play_by_play(years)


def compute_synthetic_coverage_bets(pbp_df, position, stat_column):
    """
    Walk forward through every game a position group's players were targeted in: using only
    data strictly before that game, build team tendencies, a player's target share, and
    their zone/man coverage splits, simulate a predicted stat line, and compare it to the
    player's own trailing average (the same synthetic-line approach used for the trend
    model, so results are comparable). Records what actually happened for grading.
    """
    pass_plays = pbp_df[pbp_df["pass"] == 1].copy()
    receiver_games = (
        pass_plays[pass_plays["receiver_player_id"].notna()][
            ["receiver_player_id", "receiver_player_name", "posteam", "defteam", "season", "week"]
        ]
        .drop_duplicates()
        .sort_values(["receiver_player_id", "season", "week"])
    )

    cutoffs = sorted(set(zip(receiver_games["season"], receiver_games["week"])))
    off_by_cutoff, def_by_cutoff = {}, {}
    for season, week in cutoffs:
        prior_pbp = pbp_df[(pbp_df["season"] < season) | ((pbp_df["season"] == season) & (pbp_df["week"] < week))]
        off_by_cutoff[(season, week)] = team_offensive_tendencies(prior_pbp, TEAM_TENDENCY_GAMES_WINDOW)
        def_by_cutoff[(season, week)] = team_defensive_tendencies(prior_pbp, TEAM_TENDENCY_GAMES_WINDOW)

    results = []
    for player_id, player_games in receiver_games.groupby("receiver_player_id"):
        player_games = player_games.reset_index(drop=True)
        player_targets_all = pass_plays[pass_plays["receiver_player_id"] == player_id]

        for i in range(len(player_games)):
            row = player_games.iloc[i]
            season, week, offense_team, defense_team = row["season"], row["week"], row["posteam"], row["defteam"]

            prior_games = player_games.iloc[:i]
            if len(prior_games) < MIN_LONG_RUN_GAMES:
                continue

            # Synthetic line: player's own trailing long-run average of the actual game stat
            prior_actuals = player_targets_all[
                player_targets_all.set_index(["season", "week"]).index.isin(
                    list(zip(prior_games["season"], prior_games["week"]))
                )
            ]
            game_level = prior_actuals.groupby(["season", "week"]).agg(
                receptions=("complete_pass", "sum"), receiving_yards=("receiving_yards", lambda s: s.fillna(0).sum())
            ).reset_index().sort_values(["season", "week"]).tail(LONG_RUN_GAMES_WINDOW)
            if game_level.empty:
                continue
            synthetic_line = game_level[stat_column].mean()
            if not synthetic_line:
                continue

            off_tendencies = off_by_cutoff.get((season, week))
            def_tendencies = def_by_cutoff.get((season, week))
            if off_tendencies is None or off_tendencies.empty or def_tendencies is None or def_tendencies.empty:
                continue
            matchup_sim = simulate_matchup_passing(off_tendencies, def_tendencies, offense_team, defense_team)
            if matchup_sim is None:
                continue

            # Target share: this player's own trailing average, computed the same walk-forward way
            prior_pass_plays = pass_plays[
                (pass_plays["season"] < season) | ((pass_plays["season"] == season) & (pass_plays["week"] < week))
            ]
            team_attempts = prior_pass_plays[prior_pass_plays["posteam"] == offense_team].groupby(["season", "week"]).size()
            player_prior_targets = prior_pass_plays[prior_pass_plays["receiver_player_id"] == player_id]
            player_attempts = player_prior_targets.groupby(["season", "week"]).size()
            target_share_by_week = (player_attempts / team_attempts).dropna().tail(TEAM_TENDENCY_GAMES_WINDOW)
            if target_share_by_week.empty:
                continue
            target_share = target_share_by_week.mean()

            # Coverage splits: this player's raw zone/man catch rate + yards/target, trailing window
            coverage_window_games = prior_games.tail(PLAYER_COVERAGE_GAMES_WINDOW)
            coverage_targets = player_prior_targets.set_index(["season", "week"])
            coverage_targets = coverage_targets[
                coverage_targets.index.isin(list(zip(coverage_window_games["season"], coverage_window_games["week"])))
            ]
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

            targets_vs_zone = matchup_sim["predicted_pass_attempts_vs_zone"] * target_share
            targets_vs_man = matchup_sim["predicted_pass_attempts_vs_man"] * target_share
            predicted_receptions = targets_vs_zone * splits["zone"]["catch_rate"] + targets_vs_man * splits["man"]["catch_rate"]
            predicted_yards = targets_vs_zone * splits["zone"]["yards_per_target"] + targets_vs_man * splits["man"]["yards_per_target"]
            predicted_value = predicted_receptions if stat_column == "receptions" else predicted_yards

            actual_row = player_targets_all[(player_targets_all["season"] == season) & (player_targets_all["week"] == week)]
            actual_value = actual_row["complete_pass"].sum() if stat_column == "receptions" else actual_row["receiving_yards"].fillna(0).sum()

            results.append({
                "player": row["receiver_player_name"],
                "season": season,
                "week": week,
                "synthetic_line": synthetic_line,
                "predicted_value": predicted_value,
                "edge_pct": (predicted_value - synthetic_line) / synthetic_line,
                "min_sample": min(splits["zone"]["targets"], splits["man"]["targets"]),
                "actual_value": actual_value,
                "actual_beat_line": actual_value > synthetic_line,
            })

    return pd.DataFrame(results)


def sweep_thresholds(results_df, thresholds, min_bets=20):
    """For each edge threshold, check the hit rate — did the predicted direction match
    whether the player actually beat their own synthetic line."""
    rows = []
    for threshold in thresholds:
        flagged = results_df[results_df["edge_pct"].abs() >= threshold]
        if len(flagged) < min_bets:
            continue
        predicted_over = flagged["edge_pct"] > 0
        correct = (predicted_over & flagged["actual_beat_line"]) | (~predicted_over & ~flagged["actual_beat_line"])
        rows.append({"threshold": threshold, "n": len(flagged), "hit_rate": round(correct.mean(), 3)})
    return pd.DataFrame(rows)
