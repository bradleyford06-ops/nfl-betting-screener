import logging
from screener.fetch_stats import build_position_stat_team_games
from model.power_ratings import ratings_from_team_games

logger = logging.getLogger(__name__)

# Maps an Odds API player-prop market key to the matching nfl_data_py stat column
# and the position(s) that stat applies to (used to compute what defenses allow).
PROP_STAT_MAP = {
    "player_pass_yds": {"stat_column": "passing_yards", "positions": ["QB"]},
    "player_pass_tds": {"stat_column": "passing_tds", "positions": ["QB"]},
    "player_pass_completions": {"stat_column": "completions", "positions": ["QB"]},
    "player_pass_attempts": {"stat_column": "attempts", "positions": ["QB"]},
    "player_rush_yds": {"stat_column": "rushing_yards", "positions": ["RB"]},
    "player_rush_tds": {"stat_column": "rushing_tds", "positions": ["RB"]},
    "player_rush_attempts": {"stat_column": "carries", "positions": ["RB"]},
    "player_receptions": {"stat_column": "receptions", "positions": ["WR", "TE", "RB"]},
    "player_reception_yds": {"stat_column": "receiving_yards", "positions": ["WR", "TE", "RB"]},
    "player_reception_tds": {"stat_column": "receiving_tds", "positions": ["WR", "TE", "RB"]},
}

# Per-(position, stat) edge thresholds, calibrated against a synthetic-line backtest
# (backtest/run_props_backtest.py, 2019-2024: each player's own trailing average stood in
# for a market line, since no free source of historical prop lines exists — see CLAUDE.md
# for what that can and can't prove). A threshold sweep showed the same flat 8%/5% used
# everywhere before was too loose across the board, and that natural volatility varies a
# lot by position even for the same stat — e.g. RB receiving yards is ~2.6x noisier than
# WR, and QB rushing yards is ~3.7x noisier than RB rushing. Combos not listed here haven't
# been backtested yet and fall back to the original flat guess.
DEFAULT_THRESHOLDS = {"player": 0.08, "defense": 0.05}
POSITION_STAT_THRESHOLDS = {
    ("WR", "receiving_yards"): {"player": 0.20, "defense": 0.08},
    ("RB", "receiving_yards"): {"player": 0.20, "defense": 0.08},
    ("TE", "receiving_yards"): {"player": 0.25, "defense": 0.03},
    ("RB", "rushing_yards"): {"player": 0.12, "defense": 0.08},
    ("QB", "passing_yards"): {"player": 0.25, "defense": 0.00},
    # QB rushing showed the weakest, noisiest signal of anything tested (54.5% best-case hit
    # rate vs. 58-62% elsewhere) — kept at the original flat threshold rather than tightened,
    # so enough picks flow through to watch for a real trend as the season provides fresh
    # data, but routed to its own "speculative" section (see SPECULATIVE_COMBOS) rather than
    # the main list. Bradley wants the information and will make his own call on these.
    ("QB", "rushing_yards"): {"player": 0.08, "defense": 0.05},
}
SPECULATIVE_COMBOS = {("QB", "rushing_yards")}

TEAM_RATING_GAMES_WINDOW = 17  # how many team-games feed the opponent-adjustment (matches the game model)
TEAM_RATING_ITERATIONS = 15

MIN_SAMPLE_SIZE = 1  # a single game is allowed, but see the threshold scaling below
FULL_CONFIDENCE_GAMES = 8  # sample size at which we stop demanding a bigger-than-normal edge


def player_current_team(weekly_df, player_name):
    """Look up the team a player most recently played for, so we know their opponent this week."""
    rows = weekly_df[weekly_df["player_display_name"] == player_name]
    if rows.empty:
        return None
    return rows.sort_values(["season", "week"]).iloc[-1]["recent_team"]


def has_nfl_history(weekly_df, player_name):
    """Whether a player has any NFL game logs at all — false for a true rookie debut,
    who we can't compute a trend for and shouldn't silently drop without a trace."""
    return not weekly_df[weekly_df["player_display_name"] == player_name].empty


def position_stat_ratings(weekly_df, position, stat_column):
    """
    Opponent-adjusted offense/defense ratings for one position+stat combo — e.g. how many
    rushing yards each team's RBs actually produce, and how many each defense actually
    allows, once you correct for the strength of who they played. Reuses the same
    iterative adjustment built for team scoring margin, just applied to this one stat.
    """
    team_games = build_position_stat_team_games(weekly_df, position, stat_column)
    return ratings_from_team_games(team_games, TEAM_RATING_GAMES_WINDOW, TEAM_RATING_ITERATIONS)


def get_position_stat_ratings(weekly_df, position, stat_column, ratings_cache=None):
    """Fetch (and cache) position_stat_ratings — many players share the same position/stat
    combo in a single screener run, so this avoids recomputing the same ratings per player."""
    if ratings_cache is None:
        return position_stat_ratings(weekly_df, position, stat_column)

    key = (position, stat_column)
    if key not in ratings_cache:
        ratings_cache[key] = position_stat_ratings(weekly_df, position, stat_column)
    return ratings_cache[key]


def player_adjusted_average(weekly_df, player_name, stat_column, ratings, games_window=8):
    """
    Average of a player's own stat over their last N games, with each individual game
    corrected for how tough that specific opponent's defense was (using the opponent-
    adjusted defensive rating). A player whose recent games happened to be against soft
    defenses gets pulled back down toward their true level, and vice versa.
    """
    player_games = weekly_df[weekly_df["player_display_name"] == player_name].sort_values(["season", "week"])
    recent = player_games.tail(games_window)
    if recent.empty:
        return None, 0

    defense_rating_by_team = ratings.set_index("team")["def_rating"]
    opponent_adjustment = recent["opponent_team"].map(defense_rating_by_team).fillna(0.0)
    adjusted_values = recent[stat_column] - opponent_adjustment
    return adjusted_values.mean(), len(recent)


def thresholds_for(position, stat_column):
    """Look up the calibrated (player, defense) edge thresholds for a position/stat combo,
    falling back to the flat default for anything not yet backtested."""
    return POSITION_STAT_THRESHOLDS.get((position, stat_column), DEFAULT_THRESHOLDS)


def required_edge_pct(sample_size, base_threshold):
    """
    How big a gap between average and line we require before trusting it, scaled up for
    thinner samples. A full 8-game sample uses the position/stat's calibrated threshold as-is;
    a single game needs roughly 2.8x that (standard error shrinks with the square root of
    sample size, so we widen the bar the same way to compensate for the extra noise).
    """
    effective_games = min(sample_size, FULL_CONFIDENCE_GAMES)
    return base_threshold * (FULL_CONFIDENCE_GAMES / effective_games) ** 0.5


def screen_player_prop(weekly_df, player_name, market_key, opponent_team, line, games_window=8, ratings_cache=None):
    """
    Compare one player prop line against the player's opponent-adjusted recent trend and
    how the opposing defense has performed against that stat/position, also opponent-adjusted.

    Returns a dict describing the edge if both signals agree it's a value bet, else None.
    Assumes the caller has already confirmed the player has some NFL history — a true
    rookie debut (zero games) should be routed to the "no data yet" list instead of here.
    """
    if market_key not in PROP_STAT_MAP:
        return None

    stat_column = PROP_STAT_MAP[market_key]["stat_column"]
    positions = PROP_STAT_MAP[market_key]["positions"]

    player_rows = weekly_df[weekly_df["player_display_name"] == player_name]
    if player_rows.empty:
        return None
    position = player_rows["position"].iloc[-1] or positions[0]

    ratings = get_position_stat_ratings(weekly_df, position, stat_column, ratings_cache)

    player_avg, sample_size = player_adjusted_average(weekly_df, player_name, stat_column, ratings, games_window)
    if player_avg is None or sample_size < MIN_SAMPLE_SIZE:
        return None

    opponent_row = ratings[ratings["team"] == opponent_team]
    if opponent_row.empty:
        return None
    opponent_row = opponent_row.iloc[0]
    league_avg = opponent_row["league_avg_score"]
    if not league_avg:
        return None
    defense_adjusted_allowed = league_avg + opponent_row["def_rating"]

    player_edge_pct = (player_avg - line) / line if line else 0
    defense_edge_pct = (defense_adjusted_allowed - league_avg) / league_avg

    same_direction = (player_edge_pct > 0 and defense_edge_pct > 0) or (
        player_edge_pct < 0 and defense_edge_pct < 0
    )
    if not same_direction:
        return None

    combo_thresholds = thresholds_for(position, stat_column)
    if (
        abs(player_edge_pct) < required_edge_pct(sample_size, combo_thresholds["player"])
        or abs(defense_edge_pct) < combo_thresholds["defense"]
    ):
        return None

    side = "Over" if player_edge_pct > 0 else "Under"
    return {
        "player": player_name,
        "market": market_key,
        "side": side,
        "line": round(line, 1),
        "player_recent_avg": round(player_avg, 1),
        "sample_size": sample_size,
        "opponent": opponent_team,
        "defense_avg_allowed": round(defense_adjusted_allowed, 1),
        "league_avg_allowed": round(league_avg, 1),
        "edge_score": round(abs(player_edge_pct) + abs(defense_edge_pct), 3),
        "speculative": (position, stat_column) in SPECULATIVE_COMBOS,
        "explanation": (
            f"{player_name} is averaging {player_avg:.1f} {stat_column.replace('_', ' ')} over "
            f"their last {sample_size} game{'s' if sample_size != 1 else ''} (opponent-adjusted), and "
            f"{opponent_team} allows {defense_adjusted_allowed:.1f} per game to {position}s once "
            f"adjusted for schedule (league avg: {league_avg:.1f}) — the {line:.1f} line looks "
            f"soft on the {side.lower()}."
        ),
    }
