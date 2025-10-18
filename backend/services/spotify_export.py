"""
Export Spotify playlists, albums, or individual tracks to CSV using the Web API.

Usage (CLI):
    python -m backend.services.spotify_exporter \
        --input https://open.spotify.com/playlist/65N5k6zoTqRRcUQ9u4HLzE \
        --output my_playlist.csv

Programmatic usage:
    from backend.services.spotify_exporter import export_spotify
    info, tracks = export_spotify("https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX", "album.csv")
"""

from __future__ import annotations
from pathlib import Path
from dotenv import load_dotenv

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Any
import requests

load_dotenv()

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_PLAYLIST_URL = "https://api.spotify.com/v1/playlists/{playlist_id}"
SPOTIFY_ALBUM_URL = "https://api.spotify.com/v1/albums/{album_id}/tracks"
SPOTIFY_TRACK_URL = "https://api.spotify.com/v1/tracks/{track_id}"

DEFAULT_FIELDS = (
    "playlist_index",
    "name",
    "artists",
    "album",
    "album_release_year",
    "duration_ms",
    "track_number",
    "disc_number",
    "isrc",
    "spotify_track_url",
    "added_at",
)


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
        year_fragment = self.album_release_date[:4]
        return year_fragment if year_fragment.isdigit() else None

    def to_row(self) -> Dict[str, str]:
        return {
            "playlist_index": str(self.playlist_index),
            "name": self.name,
            "artists": ", ".join(self.artists),
            "album": self.album,
            "album_release_year": self.album_release_year or "",
            "duration_ms": str(self.duration_ms),
            "track_number": "" if self.track_number is None else str(self.track_number),
            "disc_number": "" if self.disc_number is None else str(self.disc_number),
            "isrc": self.isrc or "",
            "spotify_track_url": self.spotify_track_url,
            "added_at": self.added_at or "",
        }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Spotify metadata to CSV.")
    parser.add_argument("--input", required=True, help="Spotify playlist, album, or track URL/ID.")
    parser.add_argument("--output", required=True, help="Path to output CSV file.")
    parser.add_argument("--fields", nargs="*", choices=DEFAULT_FIELDS, default=list(DEFAULT_FIELDS))
    parser.add_argument("--market", help="ISO market code (optional).")
    return parser


# ──────────────────────────────────────────────────────────────────────────────
# API Authentication and Helpers
# ──────────────────────────────────────────────────────────────────────────────

def extract_spotify_id(raw: str) -> tuple[str, str]:
    """Detect if it's a playlist, album, or track, and return (type, id)."""
    if "open.spotify.com" in raw:
        parts = raw.split("?")[0].rstrip("/").split("/")
        if len(parts) >= 2:
            typ, sid = parts[-2], parts[-1]
            if typ in ("playlist", "album", "track"):
                return typ, sid
    return "playlist", raw


def fetch_access_token(client_id: str, client_secret: str) -> str:
    """Retrieve a Spotify API access token."""
    response = requests.post(
        SPOTIFY_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=10,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Spotify did not return an access token.")
    return token


# ──────────────────────────────────────────────────────────────────────────────
# Fetchers for each Spotify object type
# ──────────────────────────────────────────────────────────────────────────────

def get_spotify_tracks(token: str, object_id: str, kind: str, market: Optional[str] = None) -> List[PlaylistTrack]:
    headers = {"Authorization": f"Bearer {token}"}
    params = {"market": market} if market else {}

    if kind == "playlist":
        return _get_playlist_tracks(headers, object_id, params)
    elif kind == "album":
        return _get_album_tracks(headers, object_id, params)
    elif kind == "track":
        return [_get_single_track(headers, object_id, params)]
    else:
        raise ValueError(f"Unsupported Spotify type: {kind}")


def _get_playlist_tracks(headers: dict, playlist_id: str, params: dict):
    """Fetch playlist metadata and all tracks."""
    url = SPOTIFY_PLAYLIST_URL.format(playlist_id=playlist_id)
    response = requests.get(url, headers=headers, params=params, timeout=10)
    response.raise_for_status()
    payload = response.json()

    # 🎵 Playlist metadata
    playlist_info = {
        "id": playlist_id,
        "name": payload.get("name"),
        "description": payload.get("description"),
        "owner": (payload.get("owner") or {}).get("display_name"),
        "total_tracks": (payload.get("tracks") or {}).get("total"),
    }

    # 🎶 Tracks
    playlist_tracks: List[PlaylistTrack] = []
    tracks_payload = payload.get("tracks") or {}
    index = 1

    while True:
        items = tracks_payload.get("items") or []
        for item in items:
            track = item.get("track") or {}
            playlist_tracks.append(_parse_track(item, track, index))
            index += 1
        next_url = tracks_payload.get("next")
        if not next_url:
            break
        response = requests.get(next_url, headers=headers, timeout=10)
        response.raise_for_status()
        tracks_payload = response.json()

    return playlist_info, playlist_tracks


def _get_album_tracks(headers: dict, album_id: str, params: dict) -> List[PlaylistTrack]:
    """Fetch *all* tracks from an album, following pagination if necessary."""
    album_info = requests.get(
        f"https://api.spotify.com/v1/albums/{album_id}",
        headers=headers, params=params, timeout=10
    )
    album_info.raise_for_status()
    album_payload = album_info.json()

    tracks: List[PlaylistTrack] = []
    index = 1
    next_url = SPOTIFY_ALBUM_URL.format(album_id=album_id)

    while next_url:
        response = requests.get(next_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        for t in data.get("items", []):
            tracks.append(_parse_track({"added_at": None}, t, index, album_payload))
            index += 1

        # Follow pagination
        next_url = data.get("next")

    return tracks


def _get_single_track(headers: dict, track_id: str, params: dict) -> PlaylistTrack:
    response = requests.get(SPOTIFY_TRACK_URL.format(track_id=track_id), headers=headers, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    return _parse_track({"added_at": None}, data, 1)


def _parse_track(item: dict[str, Any], track: dict[str, Any], index: int, album_fallback: Optional[dict] = None) -> PlaylistTrack:
    artists = [a.get("name", "") for a in track.get("artists", []) if isinstance(a, dict)]
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
        added_at=str(item.get("added_at")) if item.get("added_at") else None,
    )


# ──────────────────────────────────────────────────────────────────────────────
# CSV Writer + Unified Exporter
# ──────────────────────────────────────────────────────────────────────────────

def write_csv(tracks: Iterable[PlaylistTrack], output_path: str, fields: Iterable[str],
              playlist_info: Optional[dict] = None) -> None:
    """
    Write playlist or track metadata to a CSV file.
    If playlist_info is provided, write it as a header section first.
    """
    rows = [t.to_row() for t in tracks]
    field_list = list(fields)

    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)

        # ─── Playlist metadata header ─────────────────────────────
        if playlist_info:
            writer.writerow(["Playlist Name:", playlist_info.get("name", "")])
            writer.writerow(["Owner:", playlist_info.get("owner", "")])
            writer.writerow(["Description:", playlist_info.get("description", "")])
            writer.writerow(["Total Tracks:", playlist_info.get("total_tracks", "")])
            writer.writerow([])  # Blank line before track table

        # ─── Track table ──────────────────────────────────────────
        dict_writer = csv.DictWriter(handle, fieldnames=field_list)
        dict_writer.writeheader()
        for row in rows:
            dict_writer.writerow({k: row.get(k, "") for k in field_list})


def export_spotify(url: str, output_path: str, market: Optional[str] = None, token: Optional[str] = None):
    """
    Export a playlist, album, or track to CSV.
    If `token` is provided, it will be used directly (for user-private data).
    Otherwise, client credentials flow is used.
    """
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in the environment")

    kind, object_id = extract_spotify_id(url)

    # Use user token if given, else fall back to client credentials
    if token:
        bearer_token = token
    else:
        bearer_token = fetch_access_token(client_id, client_secret)

    playlist_info = None

    # ─── Handle playlists, albums, and tracks ──────────────────────────────
    if kind == "playlist":
        playlist_info, tracks = get_spotify_tracks(bearer_token, object_id, kind, market)
        name = playlist_info.get("name", "Unknown Playlist")
    else:
        playlist_info = {"id": object_id, "name": None, "type": kind}
        tracks = get_spotify_tracks(bearer_token, object_id, kind, market)
        name = Path(output_path).stem

    write_csv(tracks, output_path, DEFAULT_FIELDS, playlist_info)

    info = {
        "type": kind,
        "id": object_id,
        "name": name,
        "track_count": len(tracks),
        "source_info": playlist_info,
    }
    return info, [t.to_row() for t in tracks]


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        export_spotify(args.input, args.output, args.market)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Exported Spotify {args.input} → {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
