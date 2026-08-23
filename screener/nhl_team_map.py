# The Odds API returns full team names ("Toronto Maple Leafs"); the NHL API and
# MoneyPuck both use short codes ("TOR"). Static and hardcoded, same reasoning as the
# NFL team map — this data essentially never changes (Arizona -> Utah in 2024 is the
# only recent example), so it's safe to hardcode rather than depend on a live lookup.
NHL_TEAM_NAME_TO_ABBR = {
    "Anaheim Ducks": "ANA", "Boston Bruins": "BOS", "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY", "Carolina Hurricanes": "CAR", "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL", "Columbus Blue Jackets": "CBJ", "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET", "Edmonton Oilers": "EDM", "Florida Panthers": "FLA",
    "Los Angeles Kings": "LAK", "Minnesota Wild": "MIN", "Montreal Canadiens": "MTL",
    "Nashville Predators": "NSH", "New Jersey Devils": "NJD", "New York Islanders": "NYI",
    "New York Rangers": "NYR", "Ottawa Senators": "OTT", "Philadelphia Flyers": "PHI",
    "Pittsburgh Penguins": "PIT", "Seattle Kraken": "SEA", "San Jose Sharks": "SJS",
    "St Louis Blues": "STL", "St. Louis Blues": "STL", "Tampa Bay Lightning": "TBL",
    "Toronto Maple Leafs": "TOR", "Utah Hockey Club": "UTA", "Utah Mammoth": "UTA",
    "Vancouver Canucks": "VAN", "Vegas Golden Knights": "VGK", "Washington Capitals": "WSH",
    "Winnipeg Jets": "WPG",
}


def to_nhl_abbr(full_team_name, name_map=NHL_TEAM_NAME_TO_ABBR):
    """Look up a team's abbreviation, falling back to the name itself if not found."""
    if full_team_name in name_map:
        return name_map[full_team_name]
    import logging
    logging.getLogger(__name__).warning(f"No NHL abbreviation mapping found for team '{full_team_name}'")
    return full_team_name
