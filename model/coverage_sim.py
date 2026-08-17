import logging

logger = logging.getLogger(__name__)

# NOTE (2026-08-16): the full bottom-up simulation below (team pace + pass rate + defense
# zone rate + player target share, all multiplied together, then applied to coverage-split
# efficiency) was backtested against 2022-2024 and did NOT show real signal — hit rate sat
# at 51-53% even restricted to players with 50+ charted targets per coverage bucket, and
# actually got WORSE as the required edge increased (46-48%), the opposite of what a real
# signal looks like. Diagnosis: multiplying together several independently-estimated
# quantities (team pace, pass rate, target share) compounds their individual noise rather
# than canceling it out. Kept here for reference, NOT used live. See
# predict_simplified_coverage / screen_simplified_coverage_prop further down for the
# follow-up: same core insight (zone/man efficiency splits), but volume comes from the
# player's own already-validated opponent-adjusted trend instead of being rebuilt from
# scratch — cuts three of the four compounding estimates down to one proven one.

TEAM_TENDENCY_GAMES_WINDOW = 17  # matches the game model's team rating window
PLAYER_COVERAGE_GAMES_WINDOW = 32  # ~2 seasons — coverage splits need a bigger lookback than
                                     # box-score trends, since each target gets split further
                                     # into a zone/man bucket and samples get thin fast

# Calibrated (2026-08-16) against backtest/simulate_coverage_v2.py, run across all 6
# receiving combos (WR/RB/TE x receiving_yards/receptions, 2022-2024). Every combo showed
# the same clean, monotonically increasing hit rate as the edge threshold rose — real
# signal, unlike the full model above. 0.20 sits in a consistently solid zone across all
# six (55-60% hit rate, 700-1750 bets) without over-fitting six slightly different numbers
# to a still-modest backtest sample — see the session log for the full per-combo sweep.
COVERAGE_EDGE_THRESHOLD = 0.20  # predicted value must differ from the line by 20%+ to flag
SMALL_SAMPLE_TARGET_THRESHOLD = 15  # below this many targets in a coverage bucket, flag it visibly

COVERAGE_MARKET_MAP = {
    "player_receptions": "receptions",
    "player_reception_yds": "receiving_yards",
}


def _scrimmage_plays(pbp_df):
    """Real offensive snaps only — excludes kickoffs, punts, and other non-scrimmage plays."""
    return pbp_df[(pbp_df["pass"] == 1) | (pbp_df["rush"] == 1)]


def team_offensive_tendencies(pbp_df, games_window=TEAM_TENDENCY_GAMES_WINDOW):
    """
    Per team: recent average offensive plays per game and pass rate. This is a team's own
    pace/play-calling identity, not opponent-adjusted — unlike scoring margin, a team's pass
    rate isn't really "contested" by the opponent the way points are, so a simple recent
    average is more appropriate than the iterative adjustment used elsewhere.
    """
    scrimmage = _scrimmage_plays(pbp_df)
    per_game = (
        scrimmage.groupby(["posteam", "season", "week"])
        .agg(plays=("play_type", "count"), pass_plays=("pass", "sum"))
        .reset_index()
    )
    per_game["pass_rate"] = per_game["pass_plays"] / per_game["plays"]
    per_game = per_game.sort_values(["posteam", "season", "week"])

    recent = per_game.groupby("posteam").tail(games_window)
    return (
        recent.groupby("posteam")
        .agg(avg_plays=("plays", "mean"), avg_pass_rate=("pass_rate", "mean"), games=("plays", "count"))
        .reset_index()
        .rename(columns={"posteam": "team"})
    )


def team_defensive_tendencies(pbp_df, games_window=TEAM_TENDENCY_GAMES_WINDOW):
    """
    Per team: recent average defensive plays faced per game, pass rate allowed, and — the
    new piece — what fraction of their charted pass snaps were zone vs. man coverage.
    """
    scrimmage = _scrimmage_plays(pbp_df)
    per_game = (
        scrimmage.groupby(["defteam", "season", "week"])
        .agg(plays_faced=("play_type", "count"), pass_plays_faced=("pass", "sum"))
        .reset_index()
    )
    per_game["pass_rate_allowed"] = per_game["pass_plays_faced"] / per_game["plays_faced"]

    charted_pass = scrimmage[(scrimmage["pass"] == 1) & (scrimmage["defense_man_zone_type"].notna())]
    coverage_per_game = (
        charted_pass.groupby(["defteam", "season", "week"])
        .agg(
            pass_snaps_charted=("defense_man_zone_type", "count"),
            zone_snaps=("defense_man_zone_type", lambda s: (s == "ZONE_COVERAGE").sum()),
        )
        .reset_index()
    )
    coverage_per_game["zone_rate"] = coverage_per_game["zone_snaps"] / coverage_per_game["pass_snaps_charted"]

    merged = per_game.merge(
        coverage_per_game[["defteam", "season", "week", "zone_rate", "pass_snaps_charted"]],
        on=["defteam", "season", "week"],
        how="left",
    )
    merged = merged.sort_values(["defteam", "season", "week"])

    recent = merged.groupby("defteam").tail(games_window)
    return (
        recent.groupby("defteam")
        .agg(
            avg_plays_faced=("plays_faced", "mean"),
            avg_pass_rate_allowed=("pass_rate_allowed", "mean"),
            avg_zone_rate=("zone_rate", "mean"),
            games=("plays_faced", "count"),
        )
        .reset_index()
        .rename(columns={"defteam": "team"})
    )


def simulate_matchup_passing(offense_tendencies, defense_tendencies, offense_team, defense_team):
    """
    Blend one team's offensive tendencies with their opponent's defensive tendencies to
    predict this game's pass attempts for that offense, split into how many come against
    zone vs. man coverage. Zone rate is taken from the defense alone (not blended) since
    coverage-scheme choice is overwhelmingly the defense's call, unlike pass rate, which
    both sides influence.
    """
    off_row = offense_tendencies[offense_tendencies["team"] == offense_team]
    def_row = defense_tendencies[defense_tendencies["team"] == defense_team]
    if off_row.empty or def_row.empty:
        return None
    off_row, def_row = off_row.iloc[0], def_row.iloc[0]

    predicted_plays = (off_row["avg_plays"] + def_row["avg_plays_faced"]) / 2
    predicted_pass_rate = (off_row["avg_pass_rate"] + def_row["avg_pass_rate_allowed"]) / 2
    predicted_pass_attempts = predicted_plays * predicted_pass_rate
    zone_rate = def_row["avg_zone_rate"]

    return {
        "offense_team": offense_team,
        "defense_team": defense_team,
        "predicted_plays": predicted_plays,
        "predicted_pass_rate": predicted_pass_rate,
        "predicted_pass_attempts": predicted_pass_attempts,
        "predicted_pass_attempts_vs_zone": predicted_pass_attempts * zone_rate,
        "predicted_pass_attempts_vs_man": predicted_pass_attempts * (1 - zone_rate),
        "zone_rate": zone_rate,
    }


def player_target_shares(pbp_df, games_window=TEAM_TENDENCY_GAMES_WINDOW):
    """Per player: recent average share of their team's pass attempts that targeted them."""
    pass_plays = pbp_df[pbp_df["pass"] == 1]
    team_pass_attempts = (
        pass_plays.groupby(["posteam", "season", "week"]).size().rename("team_pass_attempts").reset_index()
    )
    player_targets = (
        pass_plays[pass_plays["receiver_player_id"].notna()]
        .groupby(["receiver_player_id", "posteam", "season", "week"])
        .size()
        .rename("targets")
        .reset_index()
    )
    merged = player_targets.merge(team_pass_attempts, on=["posteam", "season", "week"])
    merged["target_share"] = merged["targets"] / merged["team_pass_attempts"]
    merged = merged.sort_values(["receiver_player_id", "season", "week"])

    recent = merged.groupby("receiver_player_id").tail(games_window)
    return (
        recent.groupby("receiver_player_id")
        .agg(avg_target_share=("target_share", "mean"), games=("target_share", "count"))
        .reset_index()
        .rename(columns={"receiver_player_id": "player_id"})
    )


def player_coverage_splits(pbp_df, player_id, games_window=PLAYER_COVERAGE_GAMES_WINDOW):
    """
    Raw (unsmoothed) catch rate and yards/target for one player, split by zone vs. man
    coverage. Uses a longer game lookback than other player stats, since every target here
    gets subdivided further into a coverage bucket and samples shrink fast. Returns the
    per-bucket target count as an explicit sample size — deliberately not blended or
    shrunk toward an overall average, so a thin sample can be flagged and left to
    Bradley's judgment rather than smoothed over.
    """
    player_rows = pbp_df[(pbp_df["receiver_player_id"] == player_id) & (pbp_df["pass"] == 1)]
    played_games = player_rows[["season", "week"]].drop_duplicates().sort_values(["season", "week"]).tail(games_window)
    recent_targets = player_rows.merge(played_games, on=["season", "week"])
    recent_targets = recent_targets[recent_targets["defense_man_zone_type"].notna()]

    splits = {}
    for label, value in [("zone", "ZONE_COVERAGE"), ("man", "MAN_COVERAGE")]:
        subset = recent_targets[recent_targets["defense_man_zone_type"] == value]
        n = len(subset)
        if n == 0:
            splits[label] = {"targets": 0, "catch_rate": None, "yards_per_target": None}
            continue
        catches = subset["complete_pass"].sum()
        yards = subset["receiving_yards"].fillna(0).sum()
        splits[label] = {"targets": n, "catch_rate": catches / n, "yards_per_target": yards / n}

    return splits


def predict_receiving_stats(matchup_sim, target_share, coverage_splits):
    """
    Combine the game-level pass-attempt simulation, a player's target share, and their
    zone/man coverage splits into a predicted reception and yardage total for one player
    in one matchup. Returns None if the player has never been charted against either
    coverage type (can't simulate a matchup with zero information).
    """
    zone = coverage_splits["zone"]
    man = coverage_splits["man"]
    if zone["targets"] == 0 and man["targets"] == 0:
        return None

    targets_vs_zone = matchup_sim["predicted_pass_attempts_vs_zone"] * target_share
    targets_vs_man = matchup_sim["predicted_pass_attempts_vs_man"] * target_share

    zone_catch_rate = zone["catch_rate"] or 0
    man_catch_rate = man["catch_rate"] or 0
    zone_ypt = zone["yards_per_target"] or 0
    man_ypt = man["yards_per_target"] or 0

    predicted_receptions = targets_vs_zone * zone_catch_rate + targets_vs_man * man_catch_rate
    predicted_yards = targets_vs_zone * zone_ypt + targets_vs_man * man_ypt

    return {
        "predicted_targets_vs_zone": targets_vs_zone,
        "predicted_targets_vs_man": targets_vs_man,
        "predicted_receptions": predicted_receptions,
        "predicted_receiving_yards": predicted_yards,
        "zone_sample_size": zone["targets"],
        "man_sample_size": man["targets"],
    }


def screen_coverage_prop(player_name, market_key, matchup_sim, target_share, coverage_splits, line):
    """
    Compare a coverage-simulation prediction against a sportsbook line for one player prop.
    Covers receptions and receiving yards only — the underlying data is specifically about
    pass coverage, so it doesn't extend to rushing markets the way the trend model does.

    Returns a dict describing the edge if it clears the threshold, else None. Unlike the
    trend model, a thin sample doesn't get excluded or have its required edge scaled up —
    it's flagged directly in the output (`small_sample`) so Bradley can weigh it himself,
    per his explicit preference.
    """
    stat_column = COVERAGE_MARKET_MAP.get(market_key)
    if stat_column is None:
        return None

    prediction = predict_receiving_stats(matchup_sim, target_share, coverage_splits)
    if prediction is None:
        return None

    predicted_value = prediction["predicted_receptions"] if stat_column == "receptions" else prediction["predicted_receiving_yards"]
    if not line:
        return None

    edge_pct = (predicted_value - line) / line
    if abs(edge_pct) < COVERAGE_EDGE_THRESHOLD:
        return None

    side = "Over" if edge_pct > 0 else "Under"
    min_sample = min(prediction["zone_sample_size"], prediction["man_sample_size"])
    small_sample = min_sample < SMALL_SAMPLE_TARGET_THRESHOLD

    return {
        "player": player_name,
        "market": market_key,
        "side": side,
        "line": line,
        "predicted_value": round(predicted_value, 1),
        "edge_score": round(abs(edge_pct), 3),
        "zone_sample_size": prediction["zone_sample_size"],
        "man_sample_size": prediction["man_sample_size"],
        "small_sample": small_sample,
        "explanation": (
            f"Coverage simulation predicts {predicted_value:.1f} {stat_column.replace('_', ' ')} "
            f"({prediction['predicted_targets_vs_zone']:.1f} targets vs zone, "
            f"{prediction['predicted_targets_vs_man']:.1f} vs man) against a {line} line — "
            f"{'built on a thin sample (' + str(min_sample) + ' targets in the smaller bucket), worth double-checking. ' if small_sample else ''}"
            f"favors the {side.lower()}."
        ),
    }


# ---------------------------------------------------------------------------------
# Simplified model (2026-08-16): volume from the player's own proven trend, coverage
# as a single efficiency modifier on top of it — see the module note at the top.
# ---------------------------------------------------------------------------------

def predict_simplified_coverage(player_avg_targets, defense_zone_rate, coverage_splits):
    """
    Predict receptions and receiving yards as: the player's own recent target volume
    (already opponent-adjusted, computed the same proven way as the trend model) times a
    blended catch-rate/yards-per-target, weighted by how often this week's opponent plays
    zone vs. man. One directly-observed volume number instead of a chain of estimates.
    """
    zone, man = coverage_splits["zone"], coverage_splits["man"]
    if zone["targets"] == 0 and man["targets"] == 0:
        return None
    if player_avg_targets is None or player_avg_targets <= 0:
        return None

    zone_catch_rate, man_catch_rate = zone["catch_rate"] or 0, man["catch_rate"] or 0
    zone_ypt, man_ypt = zone["yards_per_target"] or 0, man["yards_per_target"] or 0

    blended_catch_rate = defense_zone_rate * zone_catch_rate + (1 - defense_zone_rate) * man_catch_rate
    blended_ypt = defense_zone_rate * zone_ypt + (1 - defense_zone_rate) * man_ypt

    return {
        "predicted_receptions": player_avg_targets * blended_catch_rate,
        "predicted_receiving_yards": player_avg_targets * blended_ypt,
        "zone_sample_size": zone["targets"],
        "man_sample_size": man["targets"],
    }


def screen_simplified_coverage_prop(player_name, market_key, opponent_team, player_avg_targets, defense_zone_rate, coverage_splits, line):
    """
    Compare the simplified coverage-modifier prediction against a sportsbook line.
    Same shape as screen_coverage_prop (receptions/receiving yards only, small-sample
    flagged not hidden), but built on the simplified prediction above.
    """
    stat_column = COVERAGE_MARKET_MAP.get(market_key)
    if stat_column is None:
        return None

    prediction = predict_simplified_coverage(player_avg_targets, defense_zone_rate, coverage_splits)
    if prediction is None:
        return None

    predicted_value = prediction["predicted_receptions"] if stat_column == "receptions" else prediction["predicted_receiving_yards"]
    if not line:
        return None

    edge_pct = (predicted_value - line) / line
    if abs(edge_pct) < COVERAGE_EDGE_THRESHOLD:
        return None

    side = "Over" if edge_pct > 0 else "Under"
    min_sample = min(prediction["zone_sample_size"], prediction["man_sample_size"])
    small_sample = min_sample < SMALL_SAMPLE_TARGET_THRESHOLD

    return {
        "player": player_name,
        "market": market_key,
        "side": side,
        "line": line,
        "opponent": opponent_team,
        "predicted_value": round(predicted_value, 1),
        "edge_score": round(abs(edge_pct), 3),
        "zone_sample_size": prediction["zone_sample_size"],
        "man_sample_size": prediction["man_sample_size"],
        "small_sample": small_sample,
        "explanation": (
            f"{player_name}'s recent target volume ({player_avg_targets:.1f}/game, opponent-adjusted) "
            f"blended with their zone/man efficiency split against this week's {defense_zone_rate*100:.0f}% "
            f"zone-coverage opponent predicts {predicted_value:.1f} {stat_column.replace('_', ' ')} vs a {line} line — "
            f"{'built on a thin sample (' + str(min_sample) + ' targets in the smaller bucket), worth double-checking. ' if small_sample else ''}"
            f"favors the {side.lower()}."
        ),
    }
