import os
import shutil
from pathlib import Path
from mutagen.mp3 import MP3
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from difflib import SequenceMatcher

# --- configure your Spotify API keys here ---
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# Authenticate with Spotify
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET
))

# --- Select a folder inside "songs" ---
base_dir = Path(r"C:\Users\letsbenameless\Desktop\Audio Devices\songs")
folders = [f for f in base_dir.iterdir() if f.is_dir() and f.name not in ("duds", "dud-replacements")]

print("\nAvailable folders:")
for i, folder in enumerate(folders, 1):
    print(f"{i}. {folder.name}")

choice = input("\nSelect a folder by number: ").strip()
try:
    index = int(choice) - 1
    input_dir = folders[index]
except (ValueError, IndexError):
    print("❌ Invalid selection. Exiting.")
    exit()

print(f"\n📂 Selected folder: {input_dir.name}")

# Create/locate duds folder
duds_dir = base_dir / "duds"
duds_dir.mkdir(parents=True, exist_ok=True)
print(f"🚮 Flagged songs will be copied to: {duds_dir}")

# Output lists
matched_tracks = []
flagged_tracks = []

def clean_filename_basic(raw_name: str) -> str:
    """Clean filename into a more natural Spotify search query."""
    cleaned = raw_name.replace("(", "").replace(")", "")
    cleaned = cleaned.replace("-", " ").replace("_", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned

def similarity(a: str, b: str) -> float:
    """Return string similarity 0–1."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def split_artist_title(raw_name: str):
    """Try to split into artist and title if possible."""
    if " - " in raw_name:
        artist, title = raw_name.split(" - ", 1)
        return artist.strip(), title.strip()
    return None, raw_name.strip()

# Loop through MP3 files
for mp3_file in input_dir.glob("*.mp3"):
    audio = MP3(mp3_file)
    local_duration = int(audio.info.length)  # in seconds
    raw_name = mp3_file.stem
    cleaned_name = clean_filename_basic(raw_name)

    print(f"\nChecking {raw_name} (query: {cleaned_name}, local {local_duration}s)")

    # Split guessed artist/title
    artist_guess, title_guess = split_artist_title(raw_name)

    # Search Spotify with cleaned query, fetch top 20 results
    results = sp.search(q=cleaned_name, type="track", limit=20)
    items = results["tracks"]["items"]

    if not items:
        print("   No Spotify match found.")
        flagged_tracks.append({
            "file": str(mp3_file),
            "reason": "No Spotify match found"
        })
        shutil.copy(mp3_file, duds_dir / mp3_file.name)
        print(f"   📂 Copied {mp3_file.name} to duds/")
        continue

    # Pick the best match by scoring
    best_score = -999
    spotify_track = items[0]  # fallback
    for track in items:
        track_name = track["name"]
        track_artist = track["artists"][0]["name"]
        track_duration = track["duration_ms"] // 1000

        # Base score from similarity
        score = similarity(raw_name, track_name) * 100

        # Boost if title matches closely
        if title_guess and title_guess.lower() in track_name.lower():
            score += 20

        # Boost if artist matches
        if artist_guess and artist_guess.lower() in track_artist.lower():
            score += 10

        # Penalize for duration mismatch
        diff = abs(local_duration - track_duration)
        score -= diff / 2.0

        print(f"   Candidate: {track_name} by {track_artist} ({track_duration}s) "
              f"| sim={similarity(raw_name, track_name):.2f}, dur_diff={diff}, score={score:.2f}")

        if score > best_score:
            best_score = score
            spotify_track = track

    spotify_duration = spotify_track["duration_ms"] // 1000
    spotify_name = spotify_track["name"]
    spotify_artist = spotify_track["artists"][0]["name"]

    print(f"   👉 Selected: {spotify_name} by {spotify_artist} ({spotify_duration}s)")

    # Compare durations (±5s tolerance)
    if abs(local_duration - spotify_duration) <= 5:
        print("   ✅ Duration matches within ±5s")
        matched_tracks.append({
            "file": str(mp3_file),
            "local_duration": local_duration,
            "spotify_name": spotify_name,
            "spotify_artist": spotify_artist,
            "spotify_duration": spotify_duration
        })
    else:
        print("   ❌ Duration differs")
        flagged_tracks.append({
            "file": str(mp3_file),
            "local_duration": local_duration,
            "spotify_name": spotify_name,
            "spotify_artist": spotify_artist,
            "spotify_duration": spotify_duration
        })
        shutil.copy(mp3_file, duds_dir / mp3_file.name)
        print(f"   📂 Copied {mp3_file.name} to duds/")

# Print final matched list
print("\n=== Matched Tracks ===")
if not matched_tracks:
    print("No matches found.")
else:
    for track in matched_tracks:
        print(f"{track['file']} -> {track['spotify_name']} by {track['spotify_artist']} "
              f"({track['spotify_duration']}s, local {track['local_duration']}s)")

# Write flagged songs to duds/flagged-songs.txt
flag_file = duds_dir / "flagged-songs.txt"

with open(flag_file, "w", encoding="utf-8") as f:
    if not flagged_tracks:
        f.write("No flagged songs.\n")
    else:
        f.write("=== Flagged Songs ===\n")
        for track in flagged_tracks:
            filename = Path(track['file']).name  # only filename
            if "reason" in track:
                f.write(f"{filename} -> {track['reason']}\n")
            else:
                f.write(f"{filename} -> {track['spotify_name']} by {track['spotify_artist']} "
                        f"({track['spotify_duration']}s, local {track['local_duration']}s)\n")

print(f"\n🚩 Flagged songs written to {flag_file.resolve()}")