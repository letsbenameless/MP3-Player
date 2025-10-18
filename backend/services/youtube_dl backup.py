import os
import csv
import json
import re
import time
import logging
import hashlib
import unicodedata
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import yt_dlp
from mutagen.mp4 import MP4
from pydub import AudioSegment, silence

from backend.db import models

LOGGER = logging.getLogger(__name__)

# -----------------------------
# CSV LOADER
# -----------------------------
def load_tracks_from_csv(csv_path: str) -> List[dict[str, str]]:
    """Read Spotify CSV and return rows as dicts."""
    tracks: List[dict[str, str]] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row:
                tracks.append(row)
    return tracks


# -----------------------------
# TRIM SILENCE
# -----------------------------
def trim_silence_m4a(file_path: Path) -> Path:
    """Trim leading and trailing silence from an M4A file."""
    try:
        audio = AudioSegment.from_file(file_path)

        nonsilent_ranges = silence.detect_nonsilent(
            audio,
            min_silence_len=500,
            silence_thresh=audio.dBFS - 40,
            seek_step=1
        )

        if not nonsilent_ranges:
            return file_path

        start_trim = nonsilent_ranges[0][0]
        end_trim = nonsilent_ranges[-1][1]
        if end_trim - start_trim < 500:
            return file_path

        trimmed = audio[start_trim:end_trim]

        with NamedTemporaryFile(delete=False, suffix=".m4a") as tmp:
            tmp_path = Path(tmp.name)

        trimmed.export(
            tmp_path,
            format="mp4",
            codec="aac",
            bitrate="192k",
            parameters=["-movflags", "+faststart"]
        )

        tmp_path.replace(file_path)
        LOGGER.info("Trimmed silence: %s", file_path)

    except Exception as e:
        LOGGER.error("Failed to trim silence for %s: %s", file_path, e)

    return file_path

# -----------------------------
# CHECKSUM CALCULATOR
# -----------------------------
def compute_checksum(file_path: Path, algo: str = "sha256") -> str:
    """Compute SHA256 checksum for a given file."""
    h = hashlib.new(algo)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

# -----------------------------
# DOWNLOAD + TAGGING
# -----------------------------
def sanitize_filename(name: str) -> str:
    """Convert Unicode punctuation to ASCII and strip illegal filename characters."""
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name.strip().rstrip(".")

def search_youtube_links(tracks: Iterable[dict[str, str]], cache: Optional[dict[str, str]] = None) -> list[dict[str, str]]:
    """
    Search YouTube for each track and return a list of {spotify_id, youtube_url}.
    Prioritises lyric videos first, then title+artist, then title only.
    Reuses official artist channels for faster results.
    """
    if cache is None:
        cache = {}

    results = []
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        for track in tracks:
            name = (track.get("name") or "").strip()
            artists = (track.get("artists") or "").strip()
            spotify_id = (track.get("spotify_id") or "").strip()
            if not name:
                continue

            artist_key = artists.split(",")[0].strip().lower()
            queries = [f"{name} lyrics"]
            if artists:
                queries += [f"{name} {artists} lyrics", f"{name} {artists}"]
            else:
                queries.append(name)

            best_url = None
            for q in queries:
                # Use cached official channel if available
                if artist_key in cache:
                    q = f"{cache[artist_key]} {q}"

                try:
                    info = ydl.extract_info(f"ytsearch5:{q}", download=False)
                except Exception as e:
                    LOGGER.warning("Search failed for %s: %s", q, e)
                    continue

                entries = info.get("entries", [])
                if not entries:
                    continue

                for entry in entries:
                    title = entry.get("title", "").lower()
                    desc = entry.get("description", "").lower()
                    uploader = (entry.get("uploader") or "").lower()
                    uploader_url = (entry.get("uploader_url") or "").lower()
                    fields = " ".join([title, desc, uploader])

                    # Cache official/vevo/topic channel
                    if artist_key not in cache and any(k in uploader_url for k in ("official", "vevo", "topic")):
                        cache[artist_key] = uploader_url

                    artist_tokens = [
                        a.strip().lower()
                        for a in re.split(r",|&|feat\.|ft\.|featuring", artists)
                        if a.strip()
                    ]
                    name_match = all(word in fields for word in name.lower().split())
                    artist_match = any(a in fields for a in artist_tokens) if artist_tokens else False

                    if name_match and artist_match:
                        best_url = entry.get("webpage_url")
                        break
                if best_url:
                    break

            if best_url:
                results.append({"spotify_id": spotify_id, "youtube_url": best_url})
                LOGGER.info("✅ %s → %s", name, best_url)
            else:
                LOGGER.warning("❌ No match for %s - %s", artists, name)

    return results

def get_or_cache_artist_channel(artist_name: str) -> str:
    """Get the YouTube channel for an artist, searching and caching if needed."""
    cached = models.get_artist_channel(artist_name)
    if cached:
        return cached

    print(f"🔍 Searching for YouTube channel: {artist_name}")

    cmd = [
        "yt-dlp",
        f"ytsearch5:{artist_name} official channel",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        "--quiet",
    ]

    try:
        output = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="ignore")
    except subprocess.CalledProcessError:
        print(f"⚠️ yt-dlp failed when searching for {artist_name}")
        return ""

    for line in output.splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Check if this is a channel result
        if data.get("_type") == "url" and "channel" in data.get("url", ""):
            channel_url = data["url"].replace("youtube.com//", "youtube.com/")  # clean double slash
            models.set_artist_channel(artist_name, channel_url)
            print(f"✅ Cached {artist_name}: {channel_url}")
            return channel_url

    print(f"⚠️ No channel found for {artist_name}")
    return ""

def download_and_tag(track: dict[str, str], output_dir: Path, user_id: int) -> Optional[Path]:
    """Search YouTube for the track, download as M4A, trim silence, and tag with CSV metadata."""

    name = (track.get("name") or "").strip()
    artists = (track.get("artists") or "").strip()
    album = (track.get("album") or "").strip()
    year = (track.get("album_release_year") or "").strip()
    track_number = (track.get("track_number") or "").strip()
    disc_number = (track.get("disc_number") or "").strip()
    isrc = (track.get("isrc") or "").strip()
    added_at = (track.get("added_at") or "").strip()
    spotify_id = (track.get("spotify_id") or "").strip()

    if not name:
        LOGGER.warning("Skipping track with missing name: %s", track)
        return None

    queries = [f"{name} lyrics", f"{name}"]
    if artists:
        queries += [f"{name} {artists} lyrics", f"{name} {artists}"]

    output_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "%(title).200B.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "prefer_ffmpeg": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "aac", "preferredquality": "192"},
            {"key": "FFmpegMetadata"},
        ],
        "postprocessor_args": ["-movflags", "+faststart"],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
        for q in queries:
            LOGGER.info("Searching for: %s", q)
            try:
                search_results = ydl.extract_info(f"ytsearch5:{q}", download=False)
            except Exception as e:
                LOGGER.warning("Search failed for query '%s': %s", q, e)
                continue

            if not search_results or not search_results.get("entries"):
                LOGGER.info("No results returned for query: %s", q)
                continue

            for entry in search_results.get("entries", []):
                title = entry.get("title", "").lower()
                desc = entry.get("description", "").lower()
                uploader = (entry.get("uploader") or "").lower()
                url = entry.get("webpage_url")
                youtube_id = entry.get("id")

                if not url or not youtube_id:
                    continue

                # --- Explicit match requirement ---
                artist_tokens = [
                    a.strip().lower()
                    for a in re.split(r",|&|feat\.|ft\.|featuring", artists)
                    if a.strip()
                ]
                fields = " ".join([title, desc, uploader])

                name_match = all(word in fields for word in name.lower().split())
                artist_match = any(a in fields for a in artist_tokens) if artist_tokens else False

                if not (name_match and artist_match):
                    continue  # skip loose or unrelated matches

                LOGGER.info("✅ Strong match: %s (uploader: %s)", title, uploader)

                # --- Proceed with download ---
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                m4a_path = Path(filename).with_suffix(".m4a")

                safe_name = sanitize_filename(name)
                final_path = output_dir / f"{safe_name}.m4a"

                try:
                    if m4a_path.exists():
                        m4a_path.rename(final_path)
                except Exception as e:
                    LOGGER.error("Failed to rename %s → %s: %s", m4a_path, final_path, e)
                    return None

                # Wait for file to finalize (handle ffmpeg lock)
                for _ in range(10):
                    if final_path.exists() and final_path.stat().st_size > 0:
                        try:
                            with open(final_path, "rb"):
                                break
                        except PermissionError:
                            time.sleep(1)
                    else:
                        time.sleep(1)

                trim_silence_m4a(final_path)

                try:
                    audio = MP4(final_path)
                    audio["\xa9nam"] = name
                    if artists:
                        audio["\xa9ART"] = artists
                    if album:
                        audio["\xa9alb"] = album
                    if year:
                        audio["\xa9day"] = year
                    if track_number:
                        audio["trkn"] = [(int(track_number), 0)]
                    if disc_number:
                        audio["disk"] = [(int(disc_number), 0)]
                    if isrc:
                        audio["----:com.apple.iTunes:ISRC"] = [isrc.encode("utf-8")]
                    if added_at:
                        audio["\xa9cmt"] = added_at
                    audio.save()
                    LOGGER.info("Tagged metadata for %s", name)
                except Exception as e:
                    LOGGER.warning("Skipping tag for %s: %s", name, e)

                checksum = compute_checksum(final_path)
                try:
                    record_download(
                        user_id=user_id,
                        track=track,
                        youtube_info=info,  # type: ignore
                        filepath=str(final_path),
                        checksum=checksum,
                        spotify_id=spotify_id,
                    )
                    LOGGER.info("Recorded download for %s", name)
                except Exception as e:
                    LOGGER.error("Failed to record DB entry for %s: %s", name, e)

                return final_path

            LOGGER.info("No strong match found for query: %s", q)

    LOGGER.error("❌ No suitable match found for track: %s - %s", artists, name)
    return None

def find_youtube_match(track: dict[str, str]) -> Optional[str]:
    """Find the best YouTube match for a Spotify track using strict artist/title matching."""
    name = (track.get("name") or "").strip()
    artists = (track.get("artists") or "").strip()
    if not name:
        LOGGER.warning("Skipping track with missing name: %s", track)
        return None

    queries = [f"{name} lyrics", f"{name}"]
    if artists:
        queries += [f"{name} {artists} lyrics", f"{name} {artists}"]

    artist_tokens = [
        a.strip().lower()
        for a in re.split(r",|&|feat\.|ft\.|featuring", artists)
        if a.strip()
    ]

    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        for q in queries:
            try:
                results = ydl.extract_info(f"ytsearch10:{q}", download=False)
            except Exception as e:
                LOGGER.warning("Search failed for %s: %s", q, e)
                continue

            for entry in results.get("entries", []):
                title = entry.get("title", "").lower()
                desc = entry.get("description", "").lower()
                uploader = (entry.get("uploader") or "").lower()
                fields = " ".join([title, desc, uploader])

                name_match = all(word in fields for word in name.lower().split())
                artist_match = any(a in fields for a in artist_tokens) if artist_tokens else False

                if name_match and artist_match:
                    LOGGER.info("✅ Match found: %s (uploader: %s)", title, uploader)
                    return entry.get("webpage_url")

    LOGGER.warning("❌ No match for %s - %s", artists, name)
    return None

def enrich_playlist_with_youtube(csv_path: Path) -> None:
    """Add a youtube_url column to playlist.csv by finding the best match for each track."""
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total = len(rows)
    LOGGER.info("Finding YouTube links for %d tracks...", total)

    for i, row in enumerate(rows, start=1):
        if row.get("youtube_url"):
            continue
        url = find_youtube_match(row)
        row["youtube_url"] = url or ""
        LOGGER.info("[%d/%d] %s → %s", i, total, row.get("name"), url or "No match")

    # Write back to CSV
    fieldnames = list(rows[0].keys())
    if "youtube_url" not in fieldnames:
        fieldnames.append("youtube_url")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    LOGGER.info("✅ Playlist CSV updated with YouTube links.")

# -----------------------------
# YOUTUBE-ONLY SEARCH/DOWNLOAD
# -----------------------------
def fast_youtube_download(urls: list[str], output_dir: str, username: str, trim: bool = False):
    """
    Download multiple YouTube URLs directly as M4A files.
    """
    user_id = models.get_or_create_user(username)
    outdir = Path(output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    if isinstance(urls, str):
        urls = [urls]

    for url in urls:
        LOGGER.info("▶ Downloading from: %s", url)
        try:
            _download_single_youtube(url, outdir, user_id, trim)
        except Exception as e:
            LOGGER.error("Failed to download %s: %s", url, e)


def _download_single_youtube(url: str, outdir: Path, user_id: int, trim: bool):
    """Internal single-file logic extracted from your original fast_youtube_download."""
    placeholder = outdir / "placeholder.m4a"
    for f in outdir.glob("placeholder.m4a*"):
        f.unlink()

    cmd = [
        "yt-dlp",
        "-f", "bestaudio*[ext=m4a]/bestaudio/best",
        "--extractor-args", "youtube:player_skip=1",
        "-o", str(placeholder),
        "--no-playlist",
        "--no-cache-dir",
        "--write-info-json",
        "--quiet",
        "--no-progress",
        url,
    ]
    subprocess.run(cmd, check=False)

    candidates = list(outdir.glob("placeholder.m4a*"))
    if not candidates:
        LOGGER.error("Download failed: no file created for %s", url)
        return

    final_path = max(candidates, key=lambda f: f.stat().st_mtime)
    if final_path.suffix != ".m4a":
        final_path.rename(final_path.with_suffix(".m4a"))
        final_path = final_path.with_suffix(".m4a")

    info_path = placeholder.with_suffix(".info.json")
    meta = {}
    if info_path.exists():
        try:
            meta = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    title = meta.get("title") or final_path.stem
    uploader = meta.get("uploader") or "YouTube"
    upload_date = meta.get("upload_date")
    release_year = upload_date[:4] if upload_date and len(upload_date) >= 4 else ""

    safe_title = sanitize_filename(title)
    renamed = outdir / f"{safe_title}.m4a"
    try:
        final_path.rename(renamed)
        final_path = renamed
    except Exception:
        pass

    if trim:
        trim_silence_m4a(final_path)

    audio = MP4(final_path)
    audio["\xa9nam"] = title
    audio["\xa9ART"] = uploader
    if release_year:
        audio["\xa9day"] = release_year
    audio.save()

    checksum = compute_checksum(final_path)
    track_data = {
        "spotify_id": None,
        "name": title,
        "artist": uploader,
        "album": meta.get("album", "YouTube"),
        "year": release_year,
        "duration_ms": int(meta.get("duration", 0) * 1000) if meta.get("duration") else 0,
    }
    track_id = models.get_or_create_track(track_data)
    models.execute("UPDATE tracks SET checksum=%s WHERE id=%s", (checksum, track_id))
    models.execute(
        "INSERT INTO downloads (user_id, track_id, youtube_id, filepath, bitrate, filesize_mb) VALUES (%s,%s,%s,%s,%s,%s)",
        (user_id, track_id, meta.get("id", url.split('v=')[-1]), str(final_path), 192, round(final_path.stat().st_size/1_000_000, 2))
    )
    if info_path.exists():
        info_path.unlink()

# -----------------------------
# DATABASE RECORD
# -----------------------------
def record_download(
    user_id: int,
    track: dict[str, str],
    youtube_info: dict,
    filepath: str,
    checksum: str,
    spotify_id: str,
):
    """Insert track and download info into the database using models.py helpers."""

    # --- Extract Spotify track ID safely ---
    spotify_id_clean = None
    if spotify_id:
        # Handle both full URLs and plain IDs
        if "open.spotify.com/track/" in spotify_id:
            spotify_id_clean = spotify_id.split("track/")[-1].split("?")[0].strip()
        else:
            spotify_id_clean = spotify_id.strip()
    elif track.get("spotify_url"):
        # Fallback if the CSV uses another key
        url = track["spotify_url"]
        if "open.spotify.com/track/" in url:
            spotify_id_clean = url.split("track/")[-1].split("?")[0].strip()
        else:
            spotify_id_clean = url.strip()

    # Optional debug log
    LOGGER.debug("Resolved Spotify ID: %s → %s", spotify_id, spotify_id_clean)

    # --- Build track data ---
    track_data = {
        "spotify_id": spotify_id_clean,
        "name": track.get("name"),
        "artist": track.get("artists"),
        "album": track.get("album"),
        "year": track.get("album_release_year"),
        "duration_ms": track.get("duration_ms"),
    }

    # --- Insert or get track ---
    track_id = models.get_or_create_track(track_data)

    # --- Update checksum ---
    models.execute("UPDATE tracks SET checksum=%s WHERE id=%s", (checksum, track_id))

    # --- Gather YouTube info for downloads ---
    youtube_id = youtube_info.get("id")
    filesize_bytes = youtube_info.get("filesize_approx") or youtube_info.get("filesize") or 0
    filesize_mb = round(filesize_bytes / 1_000_000, 2)

    # --- Record download entry ---
    models.execute(
        """
        INSERT INTO downloads (user_id, track_id, youtube_id, filepath, bitrate, filesize_mb)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (user_id, track_id, youtube_id, filepath, 192, filesize_mb),
    )

    # --- Log user history ---
    models.execute(
        """
        INSERT INTO user_history (user_id, track_id, action)
        VALUES (%s, %s, 'downloaded')
        """,
        (user_id, track_id),
    )

# -----------------------------
# PARALLEL DOWNLOAD EXECUTION
# -----------------------------
def process_tracks(tracks: Iterable[dict[str, str]], output_directory: str, workers: int = 4, user_id: int = 1) -> List[Path]:
    output_dir = Path(output_directory)
    downloaded: List[Path] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_track = {
            executor.submit(download_and_tag, track, output_dir, user_id): track for track in tracks
        }

        for future in as_completed(future_to_track):
            track = future_to_track[future]
            try:
                result = future.result()
                if result:
                    downloaded.append(result)
            except Exception as exc:
                LOGGER.exception("Failed to process track %s: %s", track, exc)

    if downloaded:
        LOGGER.info("Saved %d track(s) to %s as .m4a files", len(downloaded), output_dir)
    else:
        LOGGER.warning("No tracks were downloaded into %s", output_dir)

    return downloaded


def build_argument_parser():
    import argparse
    parser = argparse.ArgumentParser(description="Download audio tracks as M4A files with metadata and silence trimming")
    parser.add_argument("--csv", required=True, help="Path to Spotify-exported CSV.")
    parser.add_argument("-o", "--output-directory", default="downloads", help="Output directory for M4A files.")
    parser.add_argument("-w", "--workers", type=int, default=4, help="Number of parallel workers.")
    parser.add_argument("--user", required=True, help="Username to link downloads to.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args()

    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    tracks = load_tracks_from_csv(args.csv)
    process_tracks(tracks, args.output_directory, args.workers, args.user)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
