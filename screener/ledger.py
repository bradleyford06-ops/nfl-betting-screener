import sqlite3
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ledger.db")


def get_connection():
    """
    Open (or create) the permanent picks ledger — deliberately a separate database from the
    TTL-based cache, since this needs to survive indefinitely to track performance over the
    season, not just avoid redundant API calls.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            season INTEGER,
            week INTEGER,
            subject TEXT NOT NULL,
            market TEXT NOT NULL,
            side TEXT NOT NULL,
            line REAL,
            price REAL,
            edge_score REAL,
            opponent TEXT,
            home_team TEXT,
            away_team TEXT,
            commence_time TEXT,
            explanation TEXT,
            first_flagged_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            actual_value REAL,
            reconciled_at TEXT,
            UNIQUE(strategy, season, week, subject, market)
        )
    """)
    try:
        conn.execute("ALTER TABLE picks ADD COLUMN small_sample INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists — SQLite has no "ADD COLUMN IF NOT EXISTS"
    conn.commit()
    return conn


def record_pick(strategy, season, week, subject, market, side, line, edge_score, price=None,
                 opponent=None, home_team=None, away_team=None, commence_time=None, explanation=None,
                 small_sample=False):
    """
    Log one flagged pick, or update it if we've already logged this exact market for this
    subject this week. Upserts on (strategy, season, week, subject, market) — a pick flagged
    again on a later run refreshes the side/line/price/edge to the latest signal (what
    Bradley would actually see if he checked today), but `first_flagged_at` is preserved so
    we know when we first spotted it.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute("""
        INSERT INTO picks (strategy, season, week, subject, market, side, line, price, edge_score,
                            opponent, home_team, away_team, commence_time, explanation, small_sample,
                            first_flagged_at, last_seen_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
        ON CONFLICT(strategy, season, week, subject, market) DO UPDATE SET
            side=excluded.side,
            line=excluded.line,
            price=excluded.price,
            edge_score=excluded.edge_score,
            opponent=excluded.opponent,
            home_team=excluded.home_team,
            away_team=excluded.away_team,
            commence_time=excluded.commence_time,
            explanation=excluded.explanation,
            small_sample=excluded.small_sample,
            last_seen_at=excluded.last_seen_at
    """, (strategy, season, week, subject, market, side, line, price, edge_score,
          opponent, home_team, away_team, commence_time, explanation, int(small_sample), now, now))
    conn.commit()
    conn.close()


def get_open_picks():
    """All picks not yet reconciled against a real result."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM picks WHERE status = 'open'").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def mark_result(pick_id, status, actual_value=None):
    """Record the real outcome for one pick — status is 'won', 'lost', or 'push'."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute(
        "UPDATE picks SET status = ?, actual_value = ?, reconciled_at = ? WHERE id = ?",
        (status, actual_value, now, pick_id),
    )
    conn.commit()
    conn.close()


def get_all_picks(season=None):
    """All picks, optionally filtered to one season — the raw data behind the dashboard's
    season performance view."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    if season is not None:
        rows = conn.execute("SELECT * FROM picks WHERE season = ? ORDER BY week, id", (season,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM picks ORDER BY season, week, id").fetchall()
    conn.close()
    return [dict(row) for row in rows]
