from datetime import date

MARKET_LABELS = {
    "spread": "Spread",
    "total": "Total",
    "moneyline": "Moneyline",
}


def format_email(results):
    """Build the plain-text email body: Sides & Totals section, then Player Props section."""
    lines = []
    lines.append("NFL BETTING SCREENER")
    lines.append(f"Report Date: {date.today().strftime('%A, %B %d, %Y')}")
    lines.append("=" * 60)
    lines.append("")

    games = results.get("games", [])
    props = results.get("props", [])

    if not games and not props:
        lines.append("No bets passed the screening criteria for this run.")
        return "\n".join(lines)

    if games:
        lines.append(f"SIDES, TOTALS & MONEYLINES  ({len(games)} flagged)")
        lines.append("=" * 60)
        lines.append("")
        for i, game in enumerate(games, 1):
            lines += _format_game_flag(i, game)

    if props:
        lines.append(f"PLAYER PROPS  ({len(props)} flagged)")
        lines.append("=" * 60)
        lines.append("")
        for i, prop in enumerate(props, 1):
            lines += _format_prop_flag(i, prop)

    lines.append("-" * 60)
    lines.append("Generated automatically by the NFL Betting Screener.")
    lines.append("Informational only — not a guarantee. Always bet responsibly.")

    return "\n".join(lines)


def _format_game_flag(rank, game):
    market_label = MARKET_LABELS.get(game["market"], game["market"])
    lines = []
    lines.append(f"#{rank}  {game['away_team']} @ {game['home_team']} — {market_label}: {game['side']}")
    lines.append(f"    Edge score: {game['edge_score']}")
    lines.append(f"    {game['explanation']}")
    lines.append("")
    lines.append("-" * 60)
    lines.append("")
    return lines


def _format_prop_flag(rank, prop):
    lines = []
    lines.append(f"#{rank}  {prop['player']} — {prop['market']} {prop['side']} {prop['line']} (vs {prop['opponent']})")
    lines.append(f"    Edge score: {prop['edge_score']}")
    lines.append(f"    {prop['explanation']}")
    lines.append("")
    lines.append("-" * 60)
    lines.append("")
    return lines
