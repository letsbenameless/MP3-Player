"""Generate a CSV export for a Spotify playlist using the Web API.

This utility eliminates the manual Exportify step by talking directly to the
Spotify Web API.  Provide a playlist URL or identifier and the script will
retrieve track metadata (name, artists, album, release year, duration, etc.)
and write it to a CSV file that mirrors the data required by the YouTube
exporter.

Usage example::

    python -m backend.services.playlist_exporter.py \
        --playlist https://open.spotify.com/playlist/65N5k6zoTqRRcUQ9u4HLzE?si=a9cef9229fe044f7 \
        --output my_playlist.csv

Authentication uses the Client Credentials flow and expects
``SPOTIFY_CLIENT_ID`` and ``SPOTIFY_CLIENT_SECRET`` to be present in the
environment.  You can create these values from the `Spotify Developer
Dashboard <https://developer.spotify.com/dashboard>`_.
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()  # this reads .env in the current working directory

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Any

import requests

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_PLAYLIST_URL = "https://api.spotify.com/v1/playlists/{playlist_id}"
DEFAULT_FIELDS = (
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
    """Metadata for a playlist entry."""

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
        """Return a mapping ready for CSV writing."""

        return {
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
    parser = argparse.ArgumentParser(
        description="Export Spotify playlist metadata to CSV.",
    )
    parser.add_argument(
        "--playlist",
        required=True,
        help="Spotify playlist URL or identifier.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the CSV file that will be written.",
    )
    parser.add_argument(
        "--fields",
        nargs="*",
        choices=DEFAULT_FIELDS,
        default=list(DEFAULT_FIELDS),
        help="Subset of metadata columns to include in the CSV (default: all).",
    )
    parser.add_argument(
        "--market",
        help="ISO market code for track relinking (optional).",
    )
    return parser


def extract_playlist_id(raw: str) -> str:
    """Normalize the input to a plain playlist ID."""

    if "open.spotify.com" in raw:
        parts = raw.split("?")[0].rstrip("/").split("/")
        if parts and parts[-2] == "playlist":
            return parts[-1]
    return raw


def fetch_access_token(client_id: str, client_secret: str) -> str:
    """Retrieve an OAuth token via the client credentials flow."""

    response = requests.post(
        SPOTIFY_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Spotify did not return an access token")
    return token


def get_playlist_tracks(token: str, playlist_id: str, market: Optional[str] = None) -> List[PlaylistTrack]:
    """Fetch all tracks from a playlist."""

    headers = {"Authorization": f"Bearer {token}"}
    params = {"market": market} if market else {}
    url = SPOTIFY_PLAYLIST_URL.format(playlist_id=playlist_id)

    response = requests.get(url, headers=headers, params=params, timeout=10)
    response.raise_for_status()
    payload = response.json()

    playlist_tracks: List[PlaylistTrack] = []
    tracks_payload = payload.get("tracks") or {}
    while True:
        items = tracks_payload.get("items") or []
        for item in items:
            track = item.get("track") or {}
            playlist_tracks.append(_parse_track(item, track))

        next_url = tracks_payload.get("next")
        if not next_url:
            break
        response = requests.get(next_url, headers=headers, timeout=10)
        response.raise_for_status()
        tracks_payload = response.json()

    return playlist_tracks


def _parse_track(item: dict[str, Any], track: dict[str, Any]) -> PlaylistTrack:
    artists = [a.get("name", "") for a in track.get("artists", []) if isinstance(a, dict)]
    album = track.get("album") or {}
    return PlaylistTrack(
        name=str(track.get("name", "")),
        artists=artists,
        album=str(album.get("name", "")),
        album_release_date=album.get("release_date"),
        duration_ms=int(track.get("duration_ms") or 0),
        track_number=int(track["track_number"]) if "track_number" in track else None,
        disc_number=int(track["disc_number"]) if "disc_number" in track else None,
        isrc=(track.get("external_ids") or {}).get("isrc"),
        spotify_track_url=(track.get("external_urls") or {}).get("spotify", ""),
        added_at=str(item.get("added_at")) if item.get("added_at") else None,
    )


def write_csv(tracks: Iterable[PlaylistTrack], output_path: str, fields: Iterable[str]) -> None:
    rows = [track.to_row() for track in tracks]
    field_list = list(fields)

    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=field_list)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in field_list})



def main(argv: Optional[List[str]] = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        parser.error("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in the environment")

    playlist_id = extract_playlist_id(args.playlist)

    try:
        token = fetch_access_token(client_id, client_secret)
        tracks = get_playlist_tracks(token, playlist_id, market=args.market)
        write_csv(tracks, args.output, args.fields)
    except requests.HTTPError as exc:
        print(f"Spotify API request failed: {exc.response.status_code} {exc.response.text}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - safety net for CLI usage
        print(f"Error exporting playlist: {exc}", file=sys.stderr)
        return 1

    print(f"Exported {len(tracks)} tracks to {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())