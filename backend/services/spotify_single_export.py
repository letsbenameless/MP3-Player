"""
Export Spotify playlists, albums, or individual tracks to the database.

Usage (CLI):
    python -m backend.services.spotify_single_export --input https://open.spotify.com/playlist/... --token <user_token>

This version inserts tracks, albums, and playlists directly into the MySQL
database defined in your `.env` file, linking them to the Spotify user.
"""

from __future__ import annotations
import os
import sys
import argparse
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from dotenv import load_dotenv
import requests
from backend.db import models

load_dotenv()

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_PLAYLIST_URL = "https://api.spotify.com/v1/playlists/{playlist_id}"
SPOTIFY_ALBUM_URL = "https://api.spotify.com/v1/albums/{album_id}/tracks"
SPOTIFY_TRACK_URL = "https://api.spotify.com/v1/tracks/{track_id}"


@dataclass
class PlaylistTrack:
    playlist_index: int
    name: str
    artists: List[str]
    album: str
    album_release_date: Optional[str]
    duration_ms: int
    track_number: Optional[int]
    disc_number: Optional[int]
    isrc: Optional[str]
    spotify_track_url: str
    added_at: Optional[str]

    @property
    def album_release_year(self) -> Optional[str]:
        if not self.album_release_date:
            return None
        return self.album_release_date[:4] if self.album_release_date[:4].isdigit() else None


# ──────────────────────────────────────────────────────────────
# Spotify Helpers
# ──────────────────────────────────────────────────────────────

def extract_spotify_id(raw: str) -> tuple[str, str]:
    """Return (type, id) for playlist/album/track."""
    if "open.spotify.com" in raw:
        parts = raw.split("?")[0].rstrip("/").split("/")
        if len(parts) >= 2:
            typ, sid = parts[-2], parts[-1]
            if typ in ("playlist", "album", "track"):
                return typ, sid
    return "playlist", raw


def fetch_access_token(client_id: str, client_secret: str) -> str:
    """Fetch app-level Spotify access token (client credentials)."""
    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("Spotify did not return an access token.")
    return token


def get_or_create_user_from_token(token: str) -> int:
    """Get Spotify user profile and upsert to DB."""
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get("https://api.spotify.com/v1/me", headers=headers, timeout=10)
    r.raise_for_status()
    profile = r.json()
    username = profile.get("id") or profile.get("display_name") or "unknown_user"
    return models.upsert_user(username)


# ──────────────────────────────────────────────────────────────
# Fetchers
# ──────────────────────────────────────────────────────────────

def get_spotify_tracks(token: str, object_id: str, kind: str, market: Optional[str] = None):
    headers = {"Authorization": f"Bearer {token}"}
    params = {"market": market} if market else {}

    if kind == "playlist":
        return _get_playlist_tracks(headers, object_id, params)
    elif kind == "album":
        return _get_album_tracks(headers, object_id, params)
    elif kind == "track":
        return _get_single_track(headers, object_id, params)
    else:
        raise ValueError(f"Unsupported Spotify type: {kind}")


def _get_playlist_tracks(headers: dict, playlist_id: str, params: dict):
    url = SPOTIFY_PLAYLIST_URL.format(playlist_id=playlist_id)
    r = requests.get(url, headers=headers, params=params, timeout=10)
    r.raise_for_status()
    payload = r.json()

    playlist_info = {
        "id": playlist_id,
        "name": payload.get("name"),
        "description": payload.get("description"),
        "owner": (payload.get("owner") or {}).get("display_name"),
        "total_tracks": (payload.get("tracks") or {}).get("total"),
    }

    tracks: List[PlaylistTrack] = []
    index = 1
    data = payload.get("tracks", {})
    while True:
        for item in data.get("items", []):
            track = item.get("track") or {}
            tracks.append(_parse_track(item, track, index))
            index += 1
        if not data.get("next"):
            break
        r = requests.get(data["next"], headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()

    return playlist_info, tracks


def _get_album_tracks(headers: dict, album_id: str, params: dict):
    album_meta = requests.get(
        f"https://api.spotify.com/v1/albums/{album_id}", headers=headers, timeout=10
    )
    album_meta.raise_for_status()
    meta = album_meta.json()

    album_info = {
        "spotify_id": meta.get("id"),
        "name": meta.get("name"),
        "artist": ", ".join([a["name"] for a in meta.get("artists", [])]),
        "release_year": (meta.get("release_date") or "")[:4],
        "spotify_url": (meta.get("external_urls") or {}).get("spotify", ""),
    }

    url = SPOTIFY_ALBUM_URL.format(album_id=album_id)
    tracks: List[PlaylistTrack] = []
    index = 1
    while url:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        for t in data.get("items", []):
            tracks.append(_parse_track({"added_at": None}, t, index, meta))
            index += 1
        url = data.get("next")

    return album_info, tracks


def _get_single_track(headers: dict, track_id: str, params: dict):
    r = requests.get(SPOTIFY_TRACK_URL.format(track_id=track_id), headers=headers, params=params, timeout=10)
    r.raise_for_status()
    t = r.json()
    return None, [_parse_track({"added_at": None}, t, 1)]


def _parse_track(item: dict[str, Any], track: dict[str, Any], index: int, album_fallback: Optional[dict] = None) -> PlaylistTrack:
    artists = [a.get("name", "") for a in track.get("artists", [])]
    album = track.get("album") or album_fallback or {}
    return PlaylistTrack(
        playlist_index=index,
        name=str(track.get("name", "")),
        artists=artists,
        album=str(album.get("name", "")),
        album_release_date=album.get("release_date"),
        duration_ms=int(track.get("duration_ms") or 0),
        track_number=int(track.get("track_number", 0) or 0),
        disc_number=int(track.get("disc_number", 0) or 0),
        isrc=(track.get("external_ids") or {}).get("isrc"),
        spotify_track_url=(track.get("external_urls") or {}).get("spotify", ""),
        added_at=item.get("added_at"),
    )


# ──────────────────────────────────────────────────────────────
# Database Writer
# ──────────────────────────────────────────────────────────────

def write_to_db(tracks: List[PlaylistTrack], info: Optional[dict], kind: str, user_id: int):
    total_tracks = 0

    if kind == "playlist" and info:
        owner_name = info.get("owner")
        owner_id = None
        if owner_name:
            try:
                owner_id = models.upsert_user(owner_name)
            except Exception as e:
                print(f"⚠️ Could not insert owner '{owner_name}': {e}")

        # Insert or update playlist record
        playlist_id = models.upsert_playlist({
            "user_id": user_id,       # person exporting (you)
            "name": info.get("name"),
            "spotify_id": info.get("id"),
            "owner_id": owner_id,     # original creator
        })

        # Always link the creator as the owner (if known)
        if owner_id:
            models.link_user_playlist(owner_id, playlist_id, is_owner=True)

        # Always link the exporting user (you) too
        models.link_user_playlist(user_id, playlist_id, is_owner=(user_id == owner_id))

        # Add tracks
        for t in tracks:
            track_id = models.upsert_track({
                "spotify_id": t.spotify_track_url.split("/")[-1],
                "name": t.name,
                "artist": ", ".join(t.artists),
                "album": t.album,
                "year": t.album_release_year,
                "duration_ms": t.duration_ms,
            })
            models.link_playlist_track(playlist_id, track_id, t.playlist_index)
            total_tracks += 1

    elif kind == "album" and info:
        album_id = models.upsert_album(info)
        for t in tracks:
            track_id = models.upsert_track({
                "spotify_id": t.spotify_track_url.split("/")[-1],
                "name": t.name,
                "artist": ", ".join(t.artists),
                "album": t.album,
                "year": t.album_release_year,
                "duration_ms": t.duration_ms,
            })
            models.link_album_track(album_id, track_id, t.track_number, t.disc_number)
            total_tracks += 1

    elif kind == "track":
        for t in tracks:
            track_id = models.upsert_track({
                "spotify_id": t.spotify_track_url.split("/")[-1],
                "name": t.name,
                "artist": ", ".join(t.artists),
                "album": t.album,
                "year": t.album_release_year,
                "duration_ms": t.duration_ms,
            })
            models.execute(
                "INSERT IGNORE INTO favorites (user_id, track_id) VALUES (%s, %s)",
                (user_id, track_id),
            )
            total_tracks += 1

    models.log_export(user_id, {"tracks": total_tracks, kind + "s": 1})


# ──────────────────────────────────────────────────────────────
# Unified Exporter
# ──────────────────────────────────────────────────────────────

def export_spotify(
    url: str,
    market: Optional[str] = None,
    token: Optional[str] = None,
    force_kind: Optional[str] = None,
):
    """
    Export a playlist, album, or track to the database.
    If force_kind is provided, skip type detection and use that value instead.
    """
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set")

    # Detect the Spotify object type
    kind, object_id = extract_spotify_id(url)
    if force_kind:
        kind = force_kind  # override automatic detection if specified

    # Use user token or app token
    if token:
        bearer_token = token
    else:
        bearer_token = fetch_access_token(client_id, client_secret)

    # Get or create the current user
    user_id = get_or_create_user_from_token(bearer_token)

    # Fetch data
    info, tracks = get_spotify_tracks(bearer_token, object_id, kind, market)

    # Write to DB
    write_to_db(tracks, info, kind, user_id)

    print(f"✅ Exported {len(tracks)} {kind} tracks to database for user_id={user_id}.")
    return info, tracks


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def build_argument_parser():
    p = argparse.ArgumentParser(description="Export Spotify metadata to MySQL DB.")
    p.add_argument("--input", required=True, help="Spotify playlist/album/track URL or ID")
    p.add_argument("--market", help="Optional market code")
    p.add_argument("--token", help="Spotify user access token (for private data)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        export_spotify(args.input, args.market, args.token)
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
