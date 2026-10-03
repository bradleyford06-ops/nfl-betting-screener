"""
Sends an alert email if the NHL screener had a real game to screen yesterday (Pacific
date) but never actually ran — invoked by check_nhl_daily_run.yml, which runs once daily
early in the UTC morning, safely after Pacific midnight. Checked a day in arrears
(yesterday's Pacific date, not today's) because by the time this fires, Pacific has
already rolled into a new calendar day — checking "today" at that point would ask about
tonight's games, which haven't happened yet, instead of verifying last night's.
"""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from screener.nhl_schedule_gate import first_game_time_today_pacific, PACIFIC_TZ, LAST_RUN_MARKER_PATH
from email_report.error_alert import send_nhl_no_run_alert


def main():
    yesterday_pacific = datetime.now(PACIFIC_TZ).date() - timedelta(days=1)
    first_game = first_game_time_today_pacific(today_pacific=yesterday_pacific)
    if first_game is None:
        print(f"No NHL games on {yesterday_pacific} — nothing to check.")
        return

    ran = False
    if os.path.exists(LAST_RUN_MARKER_PATH):
        with open(LAST_RUN_MARKER_PATH) as f:
            ran = f.read().strip() == str(yesterday_pacific)

    if ran:
        print(f"NHL screener ran successfully on {yesterday_pacific}.")
    else:
        print(f"NHL screener never ran on {yesterday_pacific} despite a game at {first_game.isoformat()} — sending alert.")
        send_nhl_no_run_alert(yesterday_pacific, first_game)


if __name__ == "__main__":
    main()
