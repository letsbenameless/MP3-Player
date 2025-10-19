import mysql.connector
from mysql.connector import MySQLConnection
from contextlib import contextmanager
from typing import Any, Dict, Optional, Sequence, Union
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

Params = Optional[Union[Sequence[Any], Dict[str, Any]]]


# ────────────────────────────────────────────────────────────────
# DATABASE CONNECTION
# ────────────────────────────────────────────────────────────────

@contextmanager  # type: ignore
def get_db() -> MySQLConnection:  # type: ignore
    """Context manager for MySQL connection."""
    conn = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASS"),
        database=os.getenv("DB_NAME", "mp3_player"),
    )
    try:
        yield conn  # type: ignore
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


# ────────────────────────────────────────────────────────────────
# UPSERT HELPERS
# ────────────────────────────────────────────────────────────────

def upsert_user(username: str) -> int:
    """Insert or fetch a user by username."""
    user = fetch_one("SELECT id FROM users WHERE username=%s", (username,))
    if user:
        return int(user["id"])
    return execute("INSERT INTO users (username) VALUES (%s)", (username,))


def upsert_album(data: Dict[str, Any]) -> int:
    """Insert or update an album and return its ID."""
    execute(
        """
        INSERT INTO albums (spotify_id, name, artist, release_year, spotify_url)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            artist = VALUES(artist),
            release_year = VALUES(release_year),
            spotify_url = VALUES(spotify_url)
        """,
        (
            data["spotify_id"],
            data["name"],
            data.get("artist"),
            data.get("release_year"),
            data.get("spotify_url"),
        ),
    )
    album = fetch_one("SELECT id FROM albums WHERE spotify_id=%s", (data["spotify_id"],))
    return int(album["id"]) # type: ignore


def upsert_track(track: Dict[str, Any]) -> int:
    """Insert or update a track and return its ID."""
    execute(
        """
        INSERT INTO tracks (spotify_id, name, artist, album, year, duration_ms, checksum)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            artist = VALUES(artist),
            album = VALUES(album),
            year = VALUES(year),
            duration_ms = VALUES(duration_ms),
            checksum = VALUES(checksum)
        """,
        (
            track.get("spotify_id"),
            track.get("name"),
            track.get("artist"),
            track.get("album"),
            track.get("year"),
            track.get("duration_ms"),
            track.get("checksum"),
        ),
    )
    row = fetch_one("SELECT id FROM tracks WHERE spotify_id=%s", (track["spotify_id"],))
    return int(row["id"]) # type: ignore


def upsert_playlist(data: Dict[str, Any]) -> int:
    """
    Insert or update a playlist record and return its ID.
    Expects data with keys: user_id (exporter), name, spotify_id, and optional owner_id.
    """
    execute(
        """
        INSERT INTO playlists (user_id, name, spotify_id)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            user_id = VALUES(user_id)
        """,
        (data["user_id"], data["name"], data["spotify_id"]),
    )
    row = fetch_one("SELECT id FROM playlists WHERE spotify_id=%s", (data["spotify_id"],))
    playlist_id = int(row["id"]) # type: ignore

    # Link the creator if supplied
    if "owner_id" in data and data["owner_id"] != data["user_id"]:
        link_user_playlist(data["owner_id"], playlist_id, is_owner=True)

    return playlist_id


# ────────────────────────────────────────────────────────────────
# RELATIONSHIP LINKERS
# ────────────────────────────────────────────────────────────────

def link_playlist_track(playlist_id: int, track_id: int, order: int) -> None:
    """Attach a track to a playlist (ignores duplicates)."""
    execute(
        """
        INSERT IGNORE INTO playlist_tracks (playlist_id, track_id, track_number)
        VALUES (%s, %s, %s)
        """,
        (playlist_id, track_id, order),
    )


def link_album_track(album_id: int, track_id: int, track_number: Optional[int] = None, disc_number: Optional[int] = None) -> None:
    """Link a track to an album."""
    execute(
        """
        INSERT IGNORE INTO album_tracks (album_id, track_id, track_number, disc_number)
        VALUES (%s, %s, %s, %s)
        """,
        (album_id, track_id, track_number, disc_number),
    )


def link_user_playlist(user_id: int, playlist_id: int, is_owner: bool = False) -> None:
    """Link a user to a playlist (owner or follower)."""
    execute(
        """
        INSERT IGNORE INTO user_playlists (user_id, playlist_id, is_owner)
        VALUES (%s, %s, %s)
        """,
        (user_id, playlist_id, int(is_owner)),
    )


def log_export(user_id: int, stats: Dict[str, int], status: str = "success") -> None:
    """Log an export session for a user."""
    execute(
        """
        INSERT INTO exports (user_id, total_playlists, total_albums, total_tracks, status)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            user_id,
            stats.get("playlists", 0),
            stats.get("albums", 0),
            stats.get("tracks", 0),
            status,
        ),
    )


# ────────────────────────────────────────────────────────────────
# YOUTUBE CHANNEL CACHE
# ────────────────────────────────────────────────────────────────

def get_artist_channel(artist_name: str) -> str:
    """Return cached YouTube channel URL for an artist if it exists."""
    row = fetch_one(
        "SELECT channel_url FROM youtube_channels WHERE artist_name=%s",
        (artist_name,),
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
        (artist_name, channel_url, datetime.utcnow()),
    )

def get_tracks_to_download(limit: int = 100) -> list[dict[str, Any]]:
    """Fetch all tracks that have no corresponding download entry."""
    query = """
        SELECT t.*
        FROM tracks t
        LEFT JOIN downloads d ON t.id = d.track_id
        WHERE d.track_id IS NULL
        LIMIT %s
    """
    return fetch_all(query, (limit,))
