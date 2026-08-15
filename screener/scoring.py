def rank_props(prop_flags):
    """Sort flagged player props by edge score, strongest first."""
    return sorted(prop_flags, key=lambda p: p["edge_score"], reverse=True)


def rank_games(game_flags):
    """Sort flagged spread/total/moneyline bets by edge score, strongest first."""
    return sorted(game_flags, key=lambda g: g["edge_score"], reverse=True)
