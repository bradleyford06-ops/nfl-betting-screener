import logging
import pandas as pd
from screener.fetch_stats import get_defense_allowed

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

EDGE_THRESHOLD_PCT = 0.08  # player must be trending 8%+ away from the line to matter
DEFENSE_THRESHOLD_PCT = 0.05  # defense must allow 5%+ more/less than league average to confirm


def player_current_team(weekly_df, player_name):
    """Look up the team a player most recently played for, so we know their opponent this week."""
    rows = weekly_df[weekly_df["player_display_name"] == player_name]
    if rows.empty:
        return None
    return rows.sort_values(["season", "week"]).iloc[-1]["recent_team"]


def player_recent_average(weekly_df, player_name, stat_column, games_window=8):
    """Average of a player's own stat over their last N games."""
    player_games = weekly_df[weekly_df["player_display_name"] == player_name].sort_values(
        ["season", "week"]
    )
    recent = player_games.tail(games_window)
    if recent.empty:
        return None, 0
    return recent[stat_column].mean(), len(recent)


def league_average_allowed(weekly_df, position, stat_column):
    """League-wide average of a stat allowed to a position, per team per game — the baseline
    used to judge whether one defense is unusually generous or stingy."""
    position_df = weekly_df[weekly_df["position"] == position]
    per_game = position_df.groupby(["opponent_team", "season", "week"])[stat_column].sum()
    return per_game.mean()


def screen_player_prop(weekly_df, player_name, market_key, opponent_team, line, games_window=8):
    """
    Compare one player prop line against the player's recent trend and how the
    opposing defense has performed against that stat/position.

    Returns a dict describing the edge if both signals agree it's a value bet, else None.
    """
    if market_key not in PROP_STAT_MAP:
        return None

    stat_column = PROP_STAT_MAP[market_key]["stat_column"]
    positions = PROP_STAT_MAP[market_key]["positions"]

    player_avg, sample_size = player_recent_average(weekly_df, player_name, stat_column, games_window)
    if player_avg is None or sample_size < 3:
        return None  # not enough recent games to trust an average

    # Use the player's actual position for the defense comparison when we can find it,
    # otherwise fall back to the first position associated with this market.
    player_rows = weekly_df[weekly_df["player_display_name"] == player_name]
    position = player_rows["position"].iloc[-1] if not player_rows.empty else positions[0]

    defense_allowed = get_defense_allowed(weekly_df, position, stat_column, games_window)
    team_row = defense_allowed[defense_allowed["team"] == opponent_team]
    if team_row.empty:
        return None
    defense_avg_allowed = team_row["avg_allowed"].iloc[0]

    league_avg = league_average_allowed(weekly_df, position, stat_column)
    if not league_avg:
        return None

    player_edge_pct = (player_avg - line) / line if line else 0
    defense_edge_pct = (defense_avg_allowed - league_avg) / league_avg

    same_direction = (player_edge_pct > 0 and defense_edge_pct > 0) or (
        player_edge_pct < 0 and defense_edge_pct < 0
    )

    if not same_direction:
        return None
    if abs(player_edge_pct) < EDGE_THRESHOLD_PCT or abs(defense_edge_pct) < DEFENSE_THRESHOLD_PCT:
        return None

    side = "Over" if player_edge_pct > 0 else "Under"
    return {
        "player": player_name,
        "market": market_key,
        "side": side,
        "line": line,
        "player_recent_avg": round(player_avg, 1),
        "opponent": opponent_team,
        "defense_avg_allowed": round(defense_avg_allowed, 1),
        "league_avg_allowed": round(league_avg, 1),
        "edge_score": round(abs(player_edge_pct) + abs(defense_edge_pct), 3),
        "explanation": (
            f"{player_name} is averaging {player_avg:.1f} {stat_column.replace('_', ' ')} over "
            f"their last {sample_size} games, and {opponent_team} allows {defense_avg_allowed:.1f} "
            f"per game to {position}s (league avg: {league_avg:.1f}) — the {line} line looks "
            f"soft on the {side.lower()}."
        ),
    }
