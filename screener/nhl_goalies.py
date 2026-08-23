import logging
import re
import unicodedata

from screener.fetch_odds import get_player_props, NHL_SPORT

logger = logging.getLogger(__name__)


def normalize_player_name(name):
    """Lowercase, strip accents/punctuation, collapse whitespace — for matching goalie
    names between the Odds API's player descriptions and MoneyPuck's own player names,
    which occasionally disagree on things like accents, hyphenation, or stray spacing."""
    decomposed = unicodedata.normalize("NFD", name)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    cleaned = stripped.lower().replace(".", "").replace("-", " ").replace("'", "")
    return re.sub(r"\s+", " ", cleaned).strip()


def build_goalie_name_lookup(goalie_ratings):
    """{normalized name: (playerId, team)}, for turning an Odds API goalie name into a
    MoneyPuck playerId and the team they play for."""
    return {
        normalize_player_name(row["name"]): (row["playerId"], row["playerTeam"])
        for _, row in goalie_ratings.iterrows()
    }


def most_used_goalie(goalie_ratings, team):
    """A team's own most-used goalie recently — the fallback guess for who's starting
    when there's no confirmed-starter signal yet (either too early in the day, or the
    book hasn't posted a saves line for this particular game)."""
    team_goalies = goalie_ratings[goalie_ratings["playerTeam"] == team]
    if team_goalies.empty:
        return None
    return team_goalies.sort_values("games", ascending=False).iloc[0]["playerId"]


def confirmed_goalies_by_team(event_id, name_lookup):
    """
    The starting goalie for each team in one game, as implied by which goalie(s) have a
    posted saves total (player_total_saves) — a sportsbook only posts that market for the
    goalie it expects to actually play, so this doubles as a starter-confirmation signal
    without needing a separate (and, for the official DailyFaceoff API, $2,500/month)
    data source. Returns {team: playerId} — only includes a team if exactly one of its
    goalies has a posted line; if a team has zero or more than one (the book hasn't
    committed yet), it's left out and the caller should fall back to most_used_goalie.
    """
    try:
        props = get_player_props(event_id, markets=["player_total_saves"], sport=NHL_SPORT)
    except Exception as e:
        logger.warning(f"Failed to fetch goalie saves props for event {event_id}: {e}")
        return {}

    goalies_by_team = {}
    for bookmaker in props.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market["key"] != "player_total_saves":
                continue
            for outcome in market["outcomes"]:
                if outcome["name"] != "Over":
                    continue
                goalie_name = outcome["description"]
                match = name_lookup.get(normalize_player_name(goalie_name))
                if match is None:
                    logger.debug(f"No MoneyPuck match for goalie name '{goalie_name}'")
                    continue
                player_id, team = match
                goalies_by_team.setdefault(team, set()).add(player_id)

    return {team: next(iter(ids)) for team, ids in goalies_by_team.items() if len(ids) == 1}


def resolve_starting_goalies(event_id, home_team, away_team, goalie_ratings):
    """
    Resolve both teams' starting goalies for one game: a confirmed pick from the saves-
    prop signal where available, otherwise each team's own most-used goalie recently.
    Returns (home_goalie_id, home_confirmed, away_goalie_id, away_confirmed) — the
    confirmed flags let the pick's explanation be honest about whether this is a real
    confirmation or a best guess.
    """
    name_lookup = build_goalie_name_lookup(goalie_ratings)
    confirmed = confirmed_goalies_by_team(event_id, name_lookup)

    def resolve(team):
        if team in confirmed:
            return confirmed[team], True
        return most_used_goalie(goalie_ratings, team), False

    home_goalie_id, home_confirmed = resolve(home_team)
    away_goalie_id, away_confirmed = resolve(away_team)
    return home_goalie_id, home_confirmed, away_goalie_id, away_confirmed
