#!/usr/bin/env python3
"""
NFL Betting Screener
Run: python main.py                — screen and print results
Run: python main.py --send         — screen and send email to bradleyford5@hotmail.com
Run: python main.py --props-only   — only screen player props
Run: python main.py --games-only   — only screen spreads/totals/moneylines
Run: python main.py --nhl-only     — check whether it's time for today's NHL run, and if
                                      so, screen NHL and send its own separate email
Run: python -m email_report.send --test — send a test email without running the screener

The dashboard (docs/index.html) is regenerated from the permanent ledger's currently-open
picks (not just this run's fresh results) on every full run and every --nhl-only run that
actually screens — see dashboard/build_data.py for why: NFL/CFB run at a fixed 9am while
NHL runs later, at its own dynamic time, so no single run ever has every sport's results
in memory at once.
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


def regenerate_dashboard(no_data=None):
    from screener.reconcile import summarize_season
    from screener.ledger import get_all_picks, get_open_picks
    from dashboard.build_data import build_dashboard_data
    from dashboard.generate import write_dashboard

    logger.info("Generating dashboard...")
    dashboard_data = build_dashboard_data(get_open_picks(), summarize_season(), get_all_picks(), no_data=no_data or [])
    write_dashboard(dashboard_data, "docs/index.html")


def run_nhl_only(send):
    """
    Check whether right now is when the NHL screener should fire today (see
    screener/nhl_schedule_gate.py) — this is expected to be invoked frequently
    throughout the day by its own GitHub Actions workflow, and should do nothing on
    every check except the one that actually lands in today's run window.
    """
    from screener.nhl_schedule_gate import run_window_open, mark_ran_today

    if not run_window_open():
        logger.info("Not yet time for today's NHL run.")
        return

    from screener.pipeline import run_nhl_screener, log_results_to_ledger
    from email_report.formatter import format_nhl_email

    logger.info("Running NHL screener...")
    nhl_games, nhl_puckline_speculative = run_nhl_screener()
    log_results_to_ledger({"nhl_games": nhl_games, "nhl_puckline_speculative": nhl_puckline_speculative})

    regenerate_dashboard()

    if not nhl_games and not nhl_puckline_speculative:
        logger.warning("No NHL bets passed the screening criteria tonight.")

    report = format_nhl_email(nhl_games, nhl_puckline_speculative)
    print("\n" + report)

    if send:
        from email_report.send import send_nhl_report
        logger.info("Sending NHL email...")
        send_nhl_report(nhl_games, nhl_puckline_speculative)
        logger.info("Email sent to bradleyford5@hotmail.com")

    mark_ran_today()


def main():
    parser = argparse.ArgumentParser(description="NFL Betting Screener")
    parser.add_argument("--send", action="store_true", help="Send results by email")
    parser.add_argument("--props-only", action="store_true", help="Only screen player props")
    parser.add_argument("--games-only", action="store_true", help="Only screen spreads/totals/moneylines")
    parser.add_argument("--nhl-only", action="store_true", help="Check the NHL run-time gate, and screen NHL if it's open")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    if args.nhl_only:
        run_nhl_only(args.send)
        return

    from screener.pipeline import run_screener, log_results_to_ledger
    from screener.reconcile import reconcile_all
    from email_report.formatter import format_email

    logger.info("Reconciling past picks against real results...")
    recon_summary = reconcile_all()
    if recon_summary["reconciled"]:
        logger.info(f"Reconciled {recon_summary['reconciled']} picks ({recon_summary['still_open']} still open)")
    if recon_summary.get("cfb_error") and args.send:
        from email_report.error_alert import send_partial_failure_alert
        logger.info("Sending CFB reconciliation partial-failure alert...")
        send_partial_failure_alert("CFB reconciliation", recon_summary["cfb_error"])
    if recon_summary.get("mlb_error") and args.send:
        from email_report.error_alert import send_partial_failure_alert
        logger.info("Sending MLB reconciliation partial-failure alert...")
        send_partial_failure_alert("MLB reconciliation", recon_summary["mlb_error"])

    logger.info("Running screener...")
    results = run_screener(props_only=args.props_only, games_only=args.games_only)

    if results.get("cfb_error") and args.send:
        from email_report.error_alert import send_partial_failure_alert
        logger.info("Sending CFB screening partial-failure alert...")
        send_partial_failure_alert("CFB screening", results["cfb_error"])
    if results.get("mlb_error") and args.send:
        from email_report.error_alert import send_partial_failure_alert
        logger.info("Sending MLB screening partial-failure alert...")
        send_partial_failure_alert("MLB screening", results["mlb_error"])

    log_results_to_ledger(results)

    if not args.props_only and not args.games_only:
        regenerate_dashboard(no_data=results.get("props_no_data", []))

    if not any(results[k] for k in [
        "games", "cfb_games", "cfb_totals_speculative", "mlb_games", "mlb_speculative", "props", "props_speculative", "props_coverage", "props_no_data",
    ]):
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
    _parser.add_argument("--nhl-only", action="store_true")
    _parsed, _ = _parser.parse_known_args()

    try:
        main()
    except Exception as crash:
        logger.error(f"Screener crashed: {crash}")
        if _parsed.send:
            from email_report.error_alert import send_error_alert
            send_error_alert(crash, context="Main screener run")
        raise
