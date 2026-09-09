"""Shapes ledger picks and season data into the JSON payload the dashboard's JavaScript
renders. Kept separate from HTML generation so the data structure can be tested/inspected
on its own.

Sourced from the permanent ledger's currently-open picks rather than a single run's
in-memory results — NFL/CFB run on a fixed 9am schedule while NHL runs later, at its own
dynamic time (see screener/nhl_schedule_gate.py), so no single run ever has every sport's
fresh results in memory at once. Building from the ledger means whichever run last
regenerates the dashboard always produces a complete, correct picture across all sports,
instead of the last run to fire wiping out the others' picks."""

from datetime import datetime, timedelta, timezone

GAME_MARKET_LABELS = {"spread": "Spread", "total": "Total", "moneyline": "Moneyline", "puckline": "Puck Line", "runline": "Run Line"}

# An "open" ledger pick isn't necessarily current — before the 2026-08-21 week-filter fix,
# every run flagged the entire rest of the season at once, and those future-week picks sit
# as "open" indefinitely since their games haven't happened yet to reconcile against.
# Every run re-flags (and upserts, refreshing last_seen_at) the current week's real picks
# daily, so anything not refreshed recently is stale leftover, not a live recommendation.
STALE_PICK_CUTOFF_HOURS = 72
PROP_SOURCE_LABELS = {
    "props_trend": "Trend",
    "props_speculative": "Trend (spec.)",
    "props_coverage": "Coverage",
}
NFL_GAME_STRATEGIES = {"spread", "total"}
# The Elo-based moneyline model (2026-08-30) is kept live per Bradley's explicit choice
# despite not clearing this project's usual backtest bar — see CLAUDE.md. Tagged
# speculative in the UI rather than mixed in at face value with spread/total.
NFL_SPECULATIVE_GAME_STRATEGIES = {"moneyline_speculative"}


def _game_key(home_team, away_team):
    return f"{away_team} @ {home_team}"


def _display_line(pick):
    """The line as it actually applies to the picked side, not the raw stored value.
    Spread picks always store their line anchored to the home team's perspective
    (negative = home favored) since that's what grading needs regardless of which side
    was picked — but showing that raw number next to the away team is wrong whenever the
    away side was picked: a real +3.5 underdog would display as -3.5, looking like a
    3.5-point favorite instead. Found 2026-09-08 when Bradley caught exactly this on a
    South Florida @ Army pick. Other markets (total, moneyline, runline/puckline) aren't
    home-anchored this way and pass through unchanged."""
    line = pick.get("line")
    if pick["market"] == "spread" and line is not None and pick["side"] != pick["home_team"]:
        return -line
    return line


def _drop_stale_picks(open_picks, cutoff_hours=STALE_PICK_CUTOFF_HOURS):
    """Keep only picks a run has actually refreshed recently — see STALE_PICK_CUTOFF_HOURS."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)
    fresh = []
    for pick in open_picks:
        last_seen = pick.get("last_seen_at")
        if last_seen and datetime.fromisoformat(last_seen) >= cutoff:
            fresh.append(pick)
    return fresh


def _sorted_week_list(weeks, has_no_data=False):
    """Sort games within each week by their strongest edge, and sort weeks chronologically."""
    week_list = []
    for (season, week), bucket in sorted(weeks.items()):
        games = list(bucket["games"].values())
        for game in games:
            edge_sources = game["game_flags"] + game.get("props", [])
            game["max_edge"] = max((g["edge_score"] for g in edge_sources), default=0)
            game["game_flags"].sort(key=lambda g: g["edge_score"], reverse=True)
            if "props" in game:
                game["props"].sort(key=lambda p: p["edge_score"], reverse=True)
        games.sort(key=lambda g: g["max_edge"], reverse=True)
        entry = {"season": season, "week": week, "games": games}
        if has_no_data:
            entry["no_data"] = bucket["no_data"]
        week_list.append(entry)
    return week_list


def build_this_week_data(open_picks, no_data=None):
    """
    Group all currently open NFL game/prop picks by (season, week), then by matchup, so
    the dashboard can show "click a game, see everything tied to it."
    """
    weeks = {}

    def get_week_bucket(season, week):
        if season is None or week is None:
            return None
        key = (season, week)
        if key not in weeks:
            weeks[key] = {"season": season, "week": week, "games": {}, "no_data": []}
        return weeks[key]

    def get_game_bucket(bucket, pick):
        game_key = _game_key(pick["home_team"], pick["away_team"])
        if game_key not in bucket["games"]:
            bucket["games"][game_key] = {
                "matchup": game_key,
                "home_team": pick["home_team"],
                "away_team": pick["away_team"],
                "commence_time": pick.get("commence_time"),
                "game_flags": [],
                "props": [],
            }
        return bucket["games"][game_key]

    for pick in open_picks:
        if pick["strategy"] not in NFL_GAME_STRATEGIES and pick["strategy"] not in NFL_SPECULATIVE_GAME_STRATEGIES:
            continue
        bucket = get_week_bucket(pick.get("season"), pick.get("week"))
        if bucket is None:
            continue
        game = get_game_bucket(bucket, pick)
        game["game_flags"].append({
            "market": pick["market"],
            "market_label": GAME_MARKET_LABELS.get(pick["market"], pick["market"]),
            "side": pick["side"],
            "line": _display_line(pick),
            "price": pick.get("price"),
            "edge_score": pick["edge_score"],
            "explanation": pick.get("explanation"),
            "speculative": pick["strategy"] in NFL_SPECULATIVE_GAME_STRATEGIES,
        })

    for pick in open_picks:
        if pick["strategy"] not in PROP_SOURCE_LABELS:
            continue
        bucket = get_week_bucket(pick.get("season"), pick.get("week"))
        if bucket is None:
            continue
        game = get_game_bucket(bucket, pick)
        game["props"].append({
            "player": pick["subject"],
            "market": pick["market"],
            "side": pick["side"],
            "line": pick.get("line"),
            "price": pick.get("price"),
            "edge_score": pick["edge_score"],
            "opponent": pick.get("opponent"),
            "source": pick["strategy"],
            "source_label": PROP_SOURCE_LABELS[pick["strategy"]],
            "small_sample": bool(pick.get("small_sample", False)),
            "explanation": pick.get("explanation"),
        })

    # no_data entries only carry a "matchup" string (away @ home), not season/week, and
    # aren't real picks so they're never persisted to the ledger — passed in fresh from
    # whichever run just screened NFL props, bucketed into the most recent week found
    if no_data and weeks:
        latest_key = max(weeks.keys())
        weeks[latest_key]["no_data"].extend(no_data)

    return _sorted_week_list(weeks, has_no_data=True)


def _build_speculative_week_data(open_picks, strategy_prefix):
    """
    Shared shape for any sport whose games are grouped as "spread/total (or moneyline/
    puck-line/total) plus a speculative bucket" — currently CFB and NHL. Kept separate
    from build_this_week_data since NFL nests props under each game and tracks a
    "no_data" list, neither of which CFB/NHL have.
    """
    weeks = {}

    def get_week_bucket(season, week):
        if season is None or week is None:
            return None
        key = (season, week)
        if key not in weeks:
            weeks[key] = {"season": season, "week": week, "games": {}}
        return weeks[key]

    def get_game_bucket(bucket, pick):
        game_key = _game_key(pick["home_team"], pick["away_team"])
        if game_key not in bucket["games"]:
            bucket["games"][game_key] = {
                "matchup": game_key,
                "home_team": pick["home_team"],
                "away_team": pick["away_team"],
                "commence_time": pick.get("commence_time"),
                "game_flags": [],
            }
        return bucket["games"][game_key]

    for pick in open_picks:
        if not pick["strategy"].startswith(strategy_prefix):
            continue
        bucket = get_week_bucket(pick.get("season"), pick.get("week"))
        if bucket is None:
            continue
        game = get_game_bucket(bucket, pick)
        game["game_flags"].append({
            "market": pick["market"],
            "market_label": GAME_MARKET_LABELS.get(pick["market"], pick["market"]),
            "side": pick["side"],
            "line": _display_line(pick),
            "price": pick.get("price"),
            "edge_score": pick["edge_score"],
            "explanation": pick.get("explanation"),
            "speculative": pick["strategy"].endswith("_speculative"),
        })

    return _sorted_week_list(weeks)


def build_cfb_week_data(open_picks):
    """CFB spread + speculative-total picks, grouped by (season, week) then matchup —
    kept as its own top-level "cfb_weeks" payload key rather than merged into "weeks",
    since CFB and NFL both have a "Week 1" that means a different set of games."""
    return _build_speculative_week_data(open_picks, "cfb_")


def build_nhl_week_data(open_picks):
    """NHL moneyline/total + speculative-puck-line picks, grouped by (season, week) then
    matchup — "week" here is repurposed to hold the game's calendar date (YYYYMMDD)
    rather than a real week number, since NHL plays most days rather than once a week
    (see run_nhl_game_screener in screener/pipeline.py)."""
    return _build_speculative_week_data(open_picks, "nhl_")


def build_mlb_week_data(open_picks):
    """MLB run-line + speculative moneyline/total picks, grouped by (season, week) then
    matchup — "week" here is repurposed to hold the game's calendar date (YYYYMMDD)
    rather than a real week number, same reasoning as NHL (MLB plays most days too)."""
    return _build_speculative_week_data(open_picks, "mlb_")


def _recent_decided_picks(all_picks, limit=100):
    """The most recently reconciled picks across every sport, for the dashboard's Recent
    Results table. Sorted by reconciled_at (a real timestamp, set the moment mark_result
    grades a pick) rather than get_all_picks()'s own (season, week, id) ordering -- that
    ordering silently broke this once MLB joined the ledger, since MLB/NHL encode "week"
    as a full calendar date (e.g. 20260906) while NFL/CFB use a real week number (e.g. 1)
    -- any MLB/NHL week value is numerically thousands of times larger than any NFL/CFB
    one, so a plain sort put every MLB/NHL pick after every NFL/CFB pick regardless of
    actual date, and taking the tail via [-100:] meant NFL/CFB picks could never appear
    at all once MLB started reconciling. Found 2026-09-07 when Bradley noticed CFB's own
    season performance numbers were updating but no CFB picks ever showed in the list.
    """
    decided = [p for p in all_picks if p["status"] != "open"]
    decided.sort(key=lambda p: p.get("reconciled_at") or "", reverse=True)
    return decided[:limit]


def build_dashboard_data(open_picks, season_summary, all_picks, no_data=None):
    """Top-level payload embedded in the dashboard HTML."""
    fresh_picks = _drop_stale_picks(open_picks)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weeks": build_this_week_data(fresh_picks, no_data),
        "cfb_weeks": build_cfb_week_data(fresh_picks),
        "nhl_weeks": build_nhl_week_data(fresh_picks),
        "mlb_weeks": build_mlb_week_data(fresh_picks),
        "season_performance": season_summary,
        "recent_picks": _recent_decided_picks(all_picks),
    }
