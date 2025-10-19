"""
Export the full Spotify library (playlists, albums, and liked songs)
for the authenticated user directly into the database.

Usage:
    from backend.services.spotify_library_export import export_full_library
    export_full_library(access_token)
"""

import requests
from backend.services.spotify_single_export import export_spotify
from backend.db import models


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def sanitize_name(name: str) -> str:
    """Clean up display name for logs and debugging."""
    return name.replace("\n", " ").strip()[:240]


def export_liked_tracks_bulk(tracks: list[dict], user_id: int) -> int:
    """
    Efficiently export liked songs in bulk to the database.
    Avoids per-track API calls by inserting directly from the metadata list.
    """
    total = 0
    for t in tracks:
        try:
            track_id = models.upsert_track({
                "spotify_id": t["spotify_id"],
                "name": t["name"],
                "artist": t["artist"],
                "album": t["album"],
                "year": t["year"],
                "duration_ms": t["duration_ms"],
                "checksum": None,
            })
            models.execute(
                "INSERT IGNORE INTO favorites (user_id, track_id) VALUES (%s, %s)",
                (user_id, track_id),
            )
            total += 1
        except Exception as e:
            print(f"⚠️ Failed to insert liked song '{t.get('name', 'Unknown')}', error: {e}")
    return total


# ──────────────────────────────────────────────────────────────
# Main Export
# ──────────────────────────────────────────────────────────────

def export_full_library(token: str):
    """
    Export playlists, albums, and liked songs for the authenticated user
    directly into the MySQL database.
    """
    headers = {"Authorization": f"Bearer {token}"}
    failed = []

    # ─── Identify User ─────────────────────────────────────────
    try:
        r = requests.get("https://api.spotify.com/v1/me", headers=headers, timeout=10)
        r.raise_for_status()
        profile = r.json()
        username = profile.get("id") or profile.get("display_name") or "unknown_user"
        user_id = models.upsert_user(username)
        print(f"👤 Logged in as Spotify user '{username}' (DB user_id={user_id})")
    except Exception as e:
        print(f"❌ Failed to get user profile: {e}")
        return

    # ─── Export Playlists ──────────────────────────────────────
    print("🎵 Fetching playlists...")
    url = "https://api.spotify.com/v1/me/playlists"
    playlist_count = 0

    while url:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"❌ Failed to fetch playlists: {e}")
            break

        for p in data.get("items", []):
            name = sanitize_name(p.get("name", "Unnamed Playlist"))
            link = (p.get("external_urls") or {}).get("spotify")
            if not link:
                continue
            print(f"   → Exporting playlist: {name}")
            try:
                export_spotify(link, token=token)
                playlist_count += 1
            except Exception as e:
                print(f"⚠️  Failed to export playlist '{name}': {e}")
                failed.append(("playlist", name, str(e)))

        url = data.get("next")

    # ─── Export Albums ─────────────────────────────────────────
    print("\n💿 Fetching saved albums...")
    url = "https://api.spotify.com/v1/me/albums"
    album_count = 0

    while url:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"❌ Failed to fetch albums: {e}")
            break

        for item in data.get("items", []):
            album = item.get("album", {})
            name = sanitize_name(album.get("name", "Unnamed Album"))
            link = (album.get("external_urls") or {}).get("spotify")
            if not link:
                continue
            print(f"   → Exporting album: {name}")
            try:
                export_spotify(link, token=token, force_kind="album")
                album_count += 1
            except Exception as e:
                print(f"⚠️  Failed to export album '{name}': {e}")
                failed.append(("album", name, str(e)))

        url = data.get("next")

    # ─── Export Liked Songs (Bulk) ─────────────────────────────
    print("\n❤️ Fetching liked songs...")
    liked_url = "https://api.spotify.com/v1/me/tracks"
    liked_total = 0

    while liked_url:
        try:
            r = requests.get(liked_url, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"❌ Failed to fetch liked songs: {e}")
            break

        liked_batch = []
        for item in data.get("items", []):
            track = item.get("track")
            if not track:
                continue

            liked_batch.append({
                "spotify_id": track.get("id"),
                "name": track.get("name"),
                "artist": ", ".join([a["name"] for a in track.get("artists", [])]),
                "album": track.get("album", {}).get("name"),
                "year": (track.get("album", {}).get("release_date") or "")[:4],
                "duration_ms": track.get("duration_ms"),
                "spotify_url": (track.get("external_urls") or {}).get("spotify"),
            })

        if liked_batch:
            inserted = export_liked_tracks_bulk(liked_batch, user_id)
            liked_total += inserted
            print(f"   → Inserted {inserted} liked songs (total so far: {liked_total})")

        liked_url = data.get("next")

    # ─── Summary ───────────────────────────────────────────────
    print("\n✅ Full library export complete!")
    print(f"   Playlists exported: {playlist_count}")
    print(f"   Albums exported:    {album_count}")
    print(f"   Liked songs:        {liked_total}")

    if failed:
        print(f"\n⚠️  {len(failed)} items failed to export:")
        for kind, name, err in failed:
            print(f"   - ({kind}) {name}: {err}")
    else:
        print("\n🎉 All items exported successfully!")
