import mysql.connector
from mysql.connector import MySQLConnection
from contextlib import contextmanager
from typing import Any, Dict, Optional, Sequence, Union
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# Type alias for execute params
Params = Optional[Union[Sequence[Any], Dict[str, Any]]]

@contextmanager # type: ignore
def get_db() -> MySQLConnection: # type: ignore
    """Context manager for MySQL connection."""
    conn = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASS"),
        database=os.getenv("DB_NAME", "mp3_player")
    )
    try:
        yield conn # type: ignore
    finally:
        conn.close()

def fetch_one(query: str, params: Params = None) -> Optional[Dict[str, Any]]:
    """Run a SELECT query and return one result as a dict."""
    with get_db() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(query, params or ())
        result = cur.fetchone()
        cur.close()
        return result


def fetch_all(query: str, params: Params = None) -> list[Dict[str, Any]]:
    """Run a SELECT query and return all results as a list of dicts."""
    with get_db() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(query, params or ())
        results = cur.fetchall()
        cur.close()
        return results


def execute(query: str, params: Params = None) -> int:
    """Run an INSERT/UPDATE/DELETE query and return lastrowid."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(query, params or ())
        conn.commit()
        last_id = cur.lastrowid
        cur.close()
        return last_id


# -----------------------------
# Model helper functions
# -----------------------------

def get_or_create_user(username: str) -> int:
    user = fetch_one("SELECT id FROM users WHERE username=%s", (username,))
    if user and "id" in user:
        return int(user["id"])
    return execute("INSERT INTO users (username) VALUES (%s)", (username,))


def get_or_create_playlist(user_id: int, name: str, spotify_id: Optional[str]) -> int:
    playlist = fetch_one("""
        SELECT id FROM playlists WHERE spotify_id=%s OR (user_id=%s AND name=%s)
    """, (spotify_id, user_id, name))
    if playlist and "id" in playlist:
        return int(playlist["id"])
    return execute("""
        INSERT INTO playlists (user_id, name, spotify_id) VALUES (%s, %s, %s)
    """, (user_id, name, spotify_id))


def get_or_create_track(track: Dict[str, Any]) -> int:
    existing = fetch_one("SELECT id FROM tracks WHERE spotify_id=%s", (track["spotify_id"],))
    if existing and "id" in existing:
        return int(existing["id"])
    return execute("""
        INSERT INTO tracks (spotify_id, name, artist, album, year, duration_ms)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (
        track.get("spotify_id"),
        track.get("name"),
        track.get("artist"),
        track.get("album"),
        track.get("year"),
        track.get("duration_ms")
    ))

def get_artist_channel(artist_name: str) -> str:
    """Return cached YouTube channel URL for an artist if it exists."""
    row = fetch_one(
        "SELECT channel_url FROM youtube_channels WHERE artist_name=%s",
        (artist_name,)
    )
    if row and row.get("channel_url"):
        return row["channel_url"]
    return ""

def set_artist_channel(artist_name: str, channel_url: str) -> None:
    """Insert or update a channel cache entry for the given artist."""
    execute(
        """
        INSERT INTO youtube_channels (artist_name, channel_url, last_checked)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            channel_url = VALUES(channel_url),
            last_checked = VALUES(last_checked)
        """,
        (artist_name, channel_url, datetime.utcnow())
    )

def link_track_to_playlist(playlist_id: int, track_id: int, order: int) -> None:
    execute("""
        INSERT IGNORE INTO playlist_tracks (playlist_id, track_id, track_number)
        VALUES (%s, %s, %s)
    """, (playlist_id, track_id, order))
