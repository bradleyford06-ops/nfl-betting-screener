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
