import sqlite3
import json
import os
import time
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "cache.db")


def get_connection():
    """Open (or create) the SQLite cache database."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            cache_key TEXT PRIMARY KEY,
            data_json TEXT,
            fetched_at REAL
        )
    """)
    conn.commit()
    return conn


def get_cached(cache_key, ttl_hours):
    """Return cached JSON data for a key if it's still fresh, else None."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT data_json, fetched_at FROM cache WHERE cache_key = ?",
            (cache_key,)
        ).fetchone()
        conn.close()

        if row is None:
            return None

        age_hours = (time.time() - row[1]) / 3600
        if age_hours > ttl_hours:
            return None

        return json.loads(row[0])
    except Exception as e:
        logger.debug(f"Cache read error for {cache_key}: {e}")
        return None


def save_cache(cache_key, data):
    """Store fetched data (must be JSON-serializable) in the cache, keyed by cache_key."""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO cache (cache_key, data_json, fetched_at) VALUES (?, ?, ?)",
            (cache_key, json.dumps(data, default=str), time.time())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"Cache write error for {cache_key}: {e}")
