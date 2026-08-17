#!/usr/bin/env python3
"""
NFL Betting Screener
Run: python main.py                — screen and print results
Run: python main.py --send         — screen and send email to bradleyford5@hotmail.com
Run: python main.py --props-only   — only screen player props
Run: python main.py --games-only   — only screen spreads/totals/moneylines
Run: python -m email_report.send --test — send a test email without running the screener
"""

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="NFL Betting Screener")
    parser.add_argument("--send", action="store_true", help="Send results by email")
    parser.add_argument("--props-only", action="store_true", help="Only screen player props")
    parser.add_argument("--games-only", action="store_true", help="Only screen spreads/totals/moneylines")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    from screener.pipeline import run_screener
    from email_report.formatter import format_email

    logger.info("Running screener...")
    results = run_screener(props_only=args.props_only, games_only=args.games_only)

    if not any(results[k] for k in ["games", "props", "props_speculative", "props_coverage", "props_no_data"]):
        logger.warning("No bets passed the screening criteria today.")
        sys.exit(0)

    report = format_email(results)
    print("\n" + report)

    if args.send:
        from email_report.send import send_report
        logger.info("Sending email...")
        send_report(results)
        logger.info("Email sent to bradleyford5@hotmail.com")


if __name__ == "__main__":
    _parser = argparse.ArgumentParser()
    _parser.add_argument("--send", action="store_true")
    _parser.add_argument("--props-only", action="store_true")
    _parser.add_argument("--games-only", action="store_true")
    _parsed, _ = _parser.parse_known_args()

    try:
        main()
    except Exception as crash:
        logger.error(f"Screener crashed: {crash}")
        if _parsed.send:
            from email_report.error_alert import send_error_alert
            send_error_alert(crash, context="Main screener run")
        raise
