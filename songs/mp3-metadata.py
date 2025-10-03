import os
from pathlib import Path
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TRCK, TPOS, TCON
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from difflib import SequenceMatcher

# --- configure your Spotify API keys here ---
CLIENT_ID = "df69f9f73425495ebef18466060a0646"
CLIENT_SECRET = "41551959a0ac47eb954a120657ac17fe"

# Authenticate with Spotify
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET
))


def clean_filename_basic(raw_name: str) -> str:
    cleaned = raw_name.replace("(", "").replace(")", "")
    cleaned = cleaned.replace("-", " ").replace("_", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def split_artist_title(raw_name: str):
    if " - " in raw_name:
        artist, title = raw_name.split(" - ", 1)
        return artist.strip(), title.strip()
    return None, raw_name.strip()


def get_best_spotify_match(filename: Path):
    audio = MP3(filename)
    local_duration = int(audio.info.length)  # seconds
    raw_name = filename.stem
    cleaned_name = clean_filename_basic(raw_name)

    artist_guess, title_guess = split_artist_title(raw_name)
    results = sp.search(q=cleaned_name, type="track", limit=20)
    items = results["tracks"]["items"]

    if not items:
        return None

    best_score = -999
    best_track = items[0]
    for track in items:
        track_name = track["name"]
        track_artists = ", ".join([a["name"] for a in track["artists"]])
        track_duration = track["duration_ms"] // 1000

        score = similarity(raw_name, track_name) * 100
        if title_guess and title_guess.lower() in track_name.lower():
            score += 20
        if artist_guess and artist_guess.lower() in track_artists.lower():
            score += 10
        score -= abs(local_duration - track_duration) / 2.0

        if score > best_score:
            best_score = score
            best_track = track

    return best_track


def select_folder(base_dir: Path):
    folders = [f for f in base_dir.iterdir() if f.is_dir() and f.name not in ("duds", "dud-replacements")]
    if not folders:
        print("❌ No folders found.")
        return None

    print("\nAvailable folders:")
    for i, folder in enumerate(folders, 1):
        print(f"{i}. {folder.name}")

    choice = input("\nSelect a folder by number: ").strip()
    try:
        index = int(choice) - 1
        if 0 <= index < len(folders):
            return folders[index]
    except ValueError:
        pass

    print("❌ Invalid selection.")
    return None


def write_metadata(file_path: Path, track):
    """Write as much Spotify metadata as possible into MP3 tags."""
    spotify_name = track["name"]
    spotify_artists = ", ".join([a["name"] for a in track["artists"]])
    spotify_album = track["album"]["name"]
    spotify_release = track["album"]["release_date"]
    spotify_track_num = track["track_number"]
    spotify_disc_num = track["disc_number"]

    # Try to get genres (sometimes empty)
    genres = []
    if track["album"]["id"]:
        album = sp.album(track["album"]["id"])
        genres = album.get("genres", [])
    genre_str = ", ".join(genres) if genres else "Unknown"

    audio = MP3(file_path, ID3=ID3)
    if audio.tags is None:
        audio.add_tags()

    audio.tags["TIT2"] = TIT2(encoding=3, text=spotify_name)
    audio.tags["TPE1"] = TPE1(encoding=3, text=spotify_artists)
    audio.tags["TALB"] = TALB(encoding=3, text=spotify_album)
    audio.tags["TDRC"] = TDRC(encoding=3, text=spotify_release)
    audio.tags["TRCK"] = TRCK(encoding=3, text=str(spotify_track_num))
    audio.tags["TPOS"] = TPOS(encoding=3, text=str(spotify_disc_num))
    audio.tags["TCON"] = TCON(encoding=3, text=genre_str)

    audio.save()


def main():
    base_dir = Path(r"C:\Users\letsbenameless\Desktop\Audio Devices\songs")
    folder_path = select_folder(base_dir)
    if not folder_path:
        return

    for mp3_file in folder_path.glob("*.mp3"):
        best_track = get_best_spotify_match(mp3_file)
        if not best_track:
            print(f"\n{mp3_file.name}: No Spotify match found.")
            continue

        spotify_name = best_track["name"]
        spotify_artists = ", ".join([a["name"] for a in best_track["artists"]])
        spotify_album = best_track["album"]["name"]
        spotify_release = best_track["album"]["release_date"]
        new_name = f"{spotify_name}.mp3"

        print(f"\nCurrent:   {mp3_file.name}")
        print(f"Suggested: {new_name}")
        print(f" → Artists: {spotify_artists}")
        print(f" → Album:   {spotify_album}")
        print(f" → Release: {spotify_release}")

        choice = input("Rename & update metadata? [y = yes, k = keep old, s = skip]: ").strip().lower()
        if choice == "y":
            new_path = mp3_file.with_name(new_name)
            mp3_file.rename(new_path)
            write_metadata(new_path, best_track)
            print(f"✅ Renamed to {new_name} and wrote full metadata.")
        elif choice == "k":
            print("⏩ Kept old name.")
        else:
            print("⏭️ Skipped.")


if __name__ == "__main__":
    main()
