import os
import logging
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from screener.fetch_nhl_stats import NHL_API_BASE, NHL_REQUEST_HEADERS

logger = logging.getLogger(__name__)

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")  # Bradley's own timezone — NHL start times are naturally described relative to it
LAST_RUN_MARKER_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "nhl_last_run_date.txt")


def _games_on_date(date_str):
    """Raw NHL API schedule for one UTC calendar date. Deliberately uncached — this
    feeds a same-day scheduling decision that needs the real, current schedule (which
    can shift due to a postponement), not a stale multi-hour-old cache entry."""
    resp = requests.get(f"{NHL_API_BASE}/schedule/{date_str}", headers=NHL_REQUEST_HEADERS, timeout=15)
    resp.raise_for_status()
    for week in resp.json().get("gameWeek", []):
        if week.get("date") == date_str:
            return week.get("games", [])
    return []


def first_game_time_today_pacific(today_pacific=None):
    """
    The earliest NHL game start time (UTC) for "today" in Pacific time. NHL start times
    swing widely by day of week — weeknight evenings around 7pm ET vs. weekend matinees
    as early as 9am PT — so this reads the real schedule rather than assuming a fixed
    time. A Pacific calendar day spans two different UTC calendar dates, so both are
    checked. Returns None if there are no NHL games today.
    """
    today_pacific = today_pacific or datetime.now(PACIFIC_TZ).date()
    day_start = datetime.combine(today_pacific, datetime.min.time(), tzinfo=PACIFIC_TZ)
    day_end = day_start + timedelta(days=1)

    candidate_utc_dates = {
        day_start.astimezone(timezone.utc).date(),
        (day_end - timedelta(seconds=1)).astimezone(timezone.utc).date(),
    }

    games_today = []
    for d in candidate_utc_dates:
        games_today.extend(_games_on_date(d.isoformat()))

    start_times = []
    for game in games_today:
        start_time_utc = game.get("startTimeUTC")
        if not start_time_utc:
            continue
        start = datetime.fromisoformat(start_time_utc.replace("Z", "+00:00"))
        if day_start <= start < day_end:
            start_times.append(start)

    return min(start_times) if start_times else None


def already_ran_today():
    """Whether the NHL screener has already run today (Pacific date) — a small marker
    file, committed back to the repo the same way docs/index.html and ledger.db already
    are, so it survives across the ephemeral GitHub Actions runners between checks."""
    if not os.path.exists(LAST_RUN_MARKER_PATH):
        return False
    with open(LAST_RUN_MARKER_PATH) as f:
        return f.read().strip() == str(datetime.now(PACIFIC_TZ).date())


def mark_ran_today():
    """Record that the NHL screener has run today — call this only after a real, full
    screening run completes successfully."""
    os.makedirs(os.path.dirname(LAST_RUN_MARKER_PATH), exist_ok=True)
    with open(LAST_RUN_MARKER_PATH, "w") as f:
        f.write(str(datetime.now(PACIFIC_TZ).date()))


def run_window_open(lead_time_minutes=240, poll_interval_minutes=225, now=None):
    """
    True if right now is when the NHL screener should actually run: starting
    `lead_time_minutes` before today's first game and lasting `poll_interval_minutes`
    (ending 15 minutes before puck drop either way, never after).

    Widened from 60/45 (2026-10-03) after a real 4-day outage: GitHub's cron for this
    workflow (`*/30 * * * *`, see nhl_screener.yml) is documented to land "every 30
    minutes," but checking actual run timestamps showed real gaps of 3-7 hours between
    executions, not 30 minutes — GitHub Actions scheduled triggers are well known to be
    delayed or dropped under load, and this cron's old */30 (the unqualified "H:00"/"H:30"
    marks) sits on the most congested timestamps on the platform. With only a 45-minute
    window and ticks landing every several hours, nearly every day's window was being
    missed entirely by chance — not a logic bug, a mismatch between an optimistic
    assumption about cron reliability and a narrow catch-window. A 4-hour-wide window
    makes it very likely at least one of a day's (now off-peak-scheduled, see the cron
    change in nhl_screener.yml) infrequent ticks lands inside it, at the cost of
    potentially screening a bit earlier than ideal relative to puck drop (goalie
    confirmations may be less final several hours out vs. one hour out) —
    already_ran_today() still guarantees only the first tick inside the window actually
    screens, so this doesn't risk duplicate runs, just a wider net to catch one.
    """
    if already_ran_today():
        return False

    now = now or datetime.now(timezone.utc)
    first_game = first_game_time_today_pacific()
    if first_game is None:
        logger.info("No NHL games today — skipping this check.")
        return False

    target_start = first_game - timedelta(minutes=lead_time_minutes)
    target_end = target_start + timedelta(minutes=poll_interval_minutes)
    is_open = target_start <= now < target_end
    logger.info(
        f"First NHL game today: {first_game.isoformat()} — run window "
        f"{target_start.isoformat()} to {target_end.isoformat()} — "
        f"{'OPEN, running now' if is_open else 'not open yet'}"
    )
    return is_open
