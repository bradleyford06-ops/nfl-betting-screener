"""Renders the interactive NFL betting screener dashboard as a single self-contained HTML
file — no build step, no external dependencies, so it works as a plain file, a GitHub Pages
page, or a published artifact link. All data is embedded as JSON; all interactivity is
plain JavaScript."""

import json
import os

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "template.html")


def generate_dashboard_html(dashboard_data):
    """Embed the dashboard data payload into the HTML template and return the full page."""
    with open(TEMPLATE_PATH) as f:
        template = f.read()
    return template.replace("__DASHBOARD_DATA__", json.dumps(dashboard_data, default=str))


def write_dashboard(dashboard_data, output_path):
    """Render and write the dashboard to a file."""
    html = generate_dashboard_html(dashboard_data)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(html)
    return output_path
