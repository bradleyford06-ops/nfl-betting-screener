"""Send a plain-text alert email if the screener run fails."""

import smtplib
import os
import traceback
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SENDER = os.getenv("GMAIL_ADDRESS")
APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT = os.getenv("RECIPIENT_EMAIL", "bradleyford5@hotmail.com")


def send_error_alert(error: Exception, context: str = ""):
    """Email an alert when the screener crashes so you know something went wrong."""
    if not SENDER or not APP_PASSWORD:
        print("ERROR ALERT: Cannot send — Gmail credentials not set")
        print(f"Error was: {error}")
        return

    subject = "NFL Betting Screener — Run Failed"
    body = f"""The NFL Betting Screener did not complete successfully and no report was sent.

What went wrong:
{context + ': ' if context else ''}{type(error).__name__}: {error}

Technical details:
{traceback.format_exc()}

What to do:
- Forward this email to your Claude Code session and ask it to investigate
- The screener will try again at its next scheduled run automatically

— NFL Betting Screener
"""

    _send(subject, body)


def send_partial_failure_alert(component: str, error_message: str):
    """
    Email an alert when one part of the screener fails but the run otherwise completes
    normally (e.g. CFB screening fails while NFL still runs and sends its email) — these
    are deliberately caught so one sport's outage can't take down the others, but that
    same design means they'd otherwise be invisible until someone notices missing
    content. Found in production 2026-08-23: CFB had been silently failing on every
    scheduled run for two days before anyone noticed.
    """
    if not SENDER or not APP_PASSWORD:
        print(f"ERROR ALERT: Cannot send — Gmail credentials not set. {component} failed: {error_message}")
        return

    subject = f"NFL Betting Screener — {component} failed (rest of the run completed)"
    body = f"""Today's run completed and the report was sent, but one part of it failed and was skipped:

{component}: {error_message}

Everything else in today's report/dashboard is unaffected and unrelated to this.

What to do:
- Forward this email to your Claude Code session and ask it to investigate
- If this keeps happening on consecutive runs, it likely needs a real fix rather than
  waiting it out — a data source outage should clear on its own within a day or two.

— NFL Betting Screener
"""
    _send(subject, body)


def send_no_run_alert():
    """
    Email an alert when no successful screener run has completed by early afternoon —
    called by check_daily_run.yml, which runs once daily safely after noon ET and checks
    whether screener.yml has succeeded yet today. Exists because GitHub occasionally
    skips a scheduled cron trigger silently (see screener.yml's own comment on this,
    recurring incidents 2026-08-28, 2026-09-02, 2026-09-06) and gives no notification
    when it happens — this is the backstop for "all of today's scheduled attempts got
    skipped," not for an error inside the run itself (send_error_alert covers that).
    """
    if not SENDER or not APP_PASSWORD:
        print("ERROR ALERT: Cannot send — Gmail credentials not set. No successful run detected today.")
        return

    subject = "NFL Betting Screener — No Run Detected Today"
    body = """No successful screener run has completed today, even though several scheduled
attempts should have fired by now.

This usually means GitHub silently skipped every one of today's scheduled cron
triggers — a known, recurring platform issue, not a bug in the screener itself.

What to do:
- Trigger a run manually: gh workflow run screener.yml
- Or forward this email to your Claude Code session and ask it to trigger and verify one

— NFL Betting Screener
"""
    _send(subject, body)


def send_nhl_no_run_alert(missed_date, first_game_time):
    """
    Email an alert when the NHL screener had a real game to screen on `missed_date` but
    never actually ran — the backstop for a silent multi-day gap like the one found
    2026-10-03: GitHub's cron for nhl_screener.yml landed every 3-7 hours instead of the
    intended 30 minutes, and combined with a too-narrow catch-window, the daily run
    window was missed for 4 days straight with zero notification, since every scheduled
    check still reported "success" (the gate correctly, if unhelpfully, decided "not yet
    time" each time — see screener/nhl_schedule_gate.py for the actual fix: a much wider
    window). Checked once daily the following morning, by which point that day's window
    has definitively closed one way or another.
    """
    if not SENDER or not APP_PASSWORD:
        print(f"ERROR ALERT: Cannot send — Gmail credentials not set. NHL did not run on {missed_date}.")
        return

    subject = "NFL Betting Screener — NHL Did Not Run Yesterday"
    body = f"""The NHL screener never actually screened yesterday ({missed_date}), even
though there was a real NHL game that day (first game: {first_game_time.isoformat()}).

Every scheduled check that day reported "success," but that just means the check ran
cleanly and correctly decided it wasn't time yet — not that a real screening ever
happened. This usually means GitHub's cron landed outside the daily catch-window every
single time that day (a known platform reliability issue — see
screener/nhl_schedule_gate.py's run_window_open).

What to do:
- Trigger a run manually right now if there's still time before tonight's games:
  gh workflow run nhl_screener.yml
- Forward this email to your Claude Code session and ask it to check whether the
  catch-window needs widening further

— NFL Betting Screener
"""
    _send(subject, body)


def _send(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SENDER
        msg["To"] = RECIPIENT

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER, APP_PASSWORD)
            server.sendmail(SENDER, RECIPIENT, msg.as_string())

        print(f"Alert email sent to {RECIPIENT}")
    except Exception as alert_error:
        print(f"ERROR ALERT: Failed to send alert email: {alert_error}")
        print(f"Original message was: {subject}")
