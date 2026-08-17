import smtplib
import os
import argparse
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

RECIPIENT = "bradleyford5@hotmail.com"
SENDER = os.getenv("GMAIL_ADDRESS")
APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")


def send_email(subject, body, recipient=RECIPIENT):
    """Send a plain-text email via Gmail SMTP using an app password from .env."""
    if not SENDER or not APP_PASSWORD:
        raise EnvironmentError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER
    msg["To"] = recipient
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER, APP_PASSWORD)
        server.sendmail(SENDER, recipient, msg.as_string())

    logger.info(f"Email sent to {recipient}")


def send_report(results):
    """Format and send the screener report email."""
    from email_report.formatter import format_email
    body = format_email(results)
    today = datetime.now().strftime("%b %d")
    subject = f"NFL Betting Screener — {today}"
    send_email(subject, body)
    return body


def send_test_email():
    """Send a test email with fake data to verify email delivery is working."""
    fake_results = {
        "games": [
            {
                "market": "spread",
                "side": "KC",
                "home_team": "KC",
                "away_team": "BUF",
                "market_line": -2.5,
                "predicted_spread": 5.5,
                "edge_score": 3.0,
                "explanation": "Model predicts KC wins by 5.5, vs. a market line implying a 2.5 home margin — 3.0 points of disagreement favors KC.",
            }
        ],
        "props": [
            {
                "player": "Test Player",
                "market": "player_rush_yds",
                "side": "Over",
                "line": 55.5,
                "opponent": "SF",
                "edge_score": 0.22,
                "explanation": "Test Player is averaging 62.0 rushing yards over their last 8 games, and SF allows 68.0 per game to RBs (league avg: 55.0) — the 55.5 line looks soft on the over.",
            }
        ],
        "props_speculative": [
            {
                "player": "Test Mobile QB",
                "market": "player_rush_yds",
                "side": "Over",
                "line": 35.5,
                "opponent": "DAL",
                "edge_score": 0.10,
                "explanation": "Test Mobile QB is averaging 42.0 rushing yards over their last 8 games, and DAL allows 46.0 per game to QBs (league avg: 34.0) — the 35.5 line looks soft on the over.",
            }
        ],
        "props_coverage": [
            {
                "player": "Test Slot WR",
                "market": "player_reception_yds",
                "side": "Over",
                "line": 45.5,
                "opponent": "GB",
                "predicted_value": 58.3,
                "edge_score": 0.28,
                "zone_sample_size": 62,
                "man_sample_size": 41,
                "small_sample": False,
                "explanation": "Test Slot WR's recent target volume (7.2/game, opponent-adjusted) blended with their zone/man efficiency split against this week's 67% zone-coverage opponent predicts 58.3 receiving yards vs a 45.5 line — favors the over.",
            }
        ],
        "props_no_data": [
            {"player": "Test Rookie", "market": "player_rush_yds", "line": 45.5, "matchup": "SF @ SEA"},
        ],
    }
    body = send_report(fake_results)
    print("Test email sent successfully.")
    print("\n--- Email Preview ---")
    print(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Send a test email")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.test:
        send_test_email()
