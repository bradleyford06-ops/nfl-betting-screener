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
    props_speculative = results.get("props_speculative", [])
    props_coverage = results.get("props_coverage", [])
    props_no_data = results.get("props_no_data", [])

    if not any([games, props, props_speculative, props_coverage, props_no_data]):
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

    if props_speculative:
        lines.append(f"PLAYER PROPS — SPECULATIVE  ({len(props_speculative)} flagged)")
        lines.append("Backtesting found weak, noisy signal for these (currently just QB rushing")
        lines.append("yards) — kept visible so you can watch for a trend as the season goes, but")
        lines.append("treat these as informational, not a recommendation.")
        lines.append("=" * 60)
        lines.append("")
        for i, prop in enumerate(props_speculative, 1):
            lines += _format_prop_flag(i, prop)

    if props_coverage:
        lines.append(f"PLAYER PROPS — COVERAGE MODEL  ({len(props_coverage)} flagged)")
        lines.append("A second, independent signal for receptions/receiving yards: player's")
        lines.append("target volume weighted by how this opponent's defense splits zone vs. man")
        lines.append("coverage, and how this player performs against each. Backtested separately")
        lines.append("from the trend model above — shown on its own rather than blended in, so you")
        lines.append("can see where the two signals agree or disagree.")
        lines.append("=" * 60)
        lines.append("")
        for i, prop in enumerate(props_coverage, 1):
            lines += _format_coverage_flag(i, prop)

    if props_no_data:
        lines.append(f"PLAYER PROPS — NO DATA YET  ({len(props_no_data)} players)")
        lines.append("Rookies or players with no NFL game history — a line exists, but there's")
        lines.append("nothing to trend on yet, so the model has no opinion on these.")
        lines.append("=" * 60)
        lines.append("")
        for entry in props_no_data:
            lines.append(f"  {entry['player']} — {entry['market']} {entry['line']} ({entry['matchup']})")
        lines.append("")
        lines.append("-" * 60)
        lines.append("")

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


def _format_coverage_flag(rank, prop):
    lines = []
    sample_note = "  [small sample]" if prop.get("small_sample") else ""
    lines.append(f"#{rank}  {prop['player']} — {prop['market']} {prop['side']} {prop['line']} (vs {prop['opponent']}){sample_note}")
    lines.append(f"    Predicted: {prop['predicted_value']}  |  Edge score: {prop['edge_score']}  |  "
                 f"Samples: {prop['zone_sample_size']} zone / {prop['man_sample_size']} man targets")
    lines.append(f"    {prop['explanation']}")
    lines.append("")
    lines.append("-" * 60)
    lines.append("")
    return lines
