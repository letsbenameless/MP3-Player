from pathlib import Path
from backend.db import models
from backend.services.youtube_downloader import download_and_tag

def get_tracks_for_playlist(playlist_name: str):
    """Fetch all tracks for a given playlist name."""
    query = """
        SELECT t.*
        FROM tracks t
        JOIN playlist_tracks pt ON t.id = pt.track_id
        JOIN playlists p ON pt.playlist_id = p.id
        WHERE p.name = %s
        ORDER BY pt.track_number
    """
    return models.fetch_all(query, (playlist_name,))

def main():
    playlist_name = "butterflies"  # e.g. "Discover Weekly"
    user_id = models.upsert_user("michael")     # or whatever username you want

    tracks = get_tracks_for_playlist(playlist_name)
    print(f"🎧 Found {len(tracks)} tracks in playlist '{playlist_name}'")

    output_dir = Path("downloads") / playlist_name
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, track in enumerate(tracks, start=1):
        print(f"\n[{i}/{len(tracks)}] Downloading {track['artist']} - {track['name']}")
        path = download_and_tag(track, output_dir, user_id)
        if path:
            print(f"✅ Saved: {path}")
        else:
            print(f"❌ Failed: {track['artist']} - {track['name']}")

if __name__ == "__main__":
    main()
