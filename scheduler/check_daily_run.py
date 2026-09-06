"""Sends an alert email if no successful screener run has completed today — invoked by
check_daily_run.yml only after that workflow's own check confirms none has, since GitHub
occasionally skips every scheduled cron trigger for a day without any notification."""

from email_report.error_alert import send_no_run_alert

if __name__ == "__main__":
    send_no_run_alert()
