"""Shapes screener results and ledger data into the JSON payload the dashboard's
JavaScript renders. Kept separate from HTML generation so the data structure can be
tested/inspected on its own."""

from datetime import datetime, timezone

GAME_MARKET_LABELS = {"spread": "Spread", "total": "Total"}
PROP_SOURCE_LABELS = {
    "props": "Trend",
    "props_speculative": "Trend (spec.)",
    "props_coverage": "Coverage",
}


def _game_key(home_team, away_team):
    return f"{away_team} @ {home_team}"


def build_this_week_data(results):
    """
    Group this run's flagged games and props by (season, week), then by matchup, so the
    dashboard can show "click a game, see everything tied to it" the way Bradley described.
    """
    weeks = {}

    def get_week_bucket(season, week):
        if season is None or week is None:
            return None
        key = (season, week)
        if key not in weeks:
            weeks[key] = {"season": season, "week": week, "games": {}, "no_data": []}
        return weeks[key]

    def get_game_bucket(bucket, flag):
        game_key = _game_key(flag["home_team"], flag["away_team"])
        if game_key not in bucket["games"]:
            bucket["games"][game_key] = {
                "matchup": game_key,
                "home_team": flag["home_team"],
                "away_team": flag["away_team"],
                "commence_time": flag.get("commence_time"),
                "game_flags": [],
                "props": [],
            }
        return bucket["games"][game_key]

    for flag in results.get("games", []):
        bucket = get_week_bucket(flag.get("season"), flag.get("week"))
        if bucket is None:
            continue
        game = get_game_bucket(bucket, flag)
        game["game_flags"].append({
            "market": flag["market"],
            "market_label": GAME_MARKET_LABELS.get(flag["market"], flag["market"]),
            "side": flag["side"],
            "line": flag.get("market_line"),
            "price": flag.get("price"),
            "edge_score": flag["edge_score"],
            "explanation": flag["explanation"],
        })

    for source_key in ("props", "props_speculative", "props_coverage"):
        for flag in results.get(source_key, []):
            bucket = get_week_bucket(flag.get("season"), flag.get("week"))
            if bucket is None:
                continue
            game = get_game_bucket(bucket, flag)
            game["props"].append({
                "player": flag["player"],
                "market": flag["market"],
                "side": flag["side"],
                "line": flag.get("line"),
                "price": flag.get("price"),
                "edge_score": flag["edge_score"],
                "opponent": flag.get("opponent"),
                "source": source_key,
                "source_label": PROP_SOURCE_LABELS[source_key],
                "small_sample": flag.get("small_sample", False),
                "explanation": flag["explanation"],
            })

    for flag in results.get("props_no_data", []):
        # no_data entries only carry a "matchup" string (away @ home), not season/week —
        # bucket them into the most recent week rather than dropping them
        if not weeks:
            continue
        latest_key = max(weeks.keys())
        weeks[latest_key]["no_data"].append(flag)

    # Sort games within each week by their strongest prop/game edge, and sort weeks chronologically
    week_list = []
    for (season, week), bucket in sorted(weeks.items()):
        games = list(bucket["games"].values())
        for game in games:
            all_edges = [g["edge_score"] for g in game["game_flags"]] + [p["edge_score"] for p in game["props"]]
            game["max_edge"] = max(all_edges) if all_edges else 0
            game["game_flags"].sort(key=lambda g: g["edge_score"], reverse=True)
            game["props"].sort(key=lambda p: p["edge_score"], reverse=True)
        games.sort(key=lambda g: g["max_edge"], reverse=True)
        week_list.append({"season": season, "week": week, "games": games, "no_data": bucket["no_data"]})

    return week_list


def build_dashboard_data(results, season_summary, all_picks):
    """Top-level payload embedded in the dashboard HTML."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weeks": build_this_week_data(results),
        "season_performance": season_summary,
        "recent_picks": [p for p in all_picks if p["status"] != "open"][-100:],
    }
