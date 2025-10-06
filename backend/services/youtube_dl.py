import os
import csv
import logging
import hashlib
from pathlib import Path
from typing import Any, Iterable, List, Optional
from tempfile import NamedTemporaryFile
from concurrent.futures import ThreadPoolExecutor, as_completed
from backend.db import models

import yt_dlp
from mutagen.mp4 import MP4
from pydub import AudioSegment, silence

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

    if not name or not artists:
        LOGGER.warning("Skipping track with missing name/artist: %s", track)
        return None

    queries = [f"{name} {artists} lyrics", f"{name} {artists}"]
    output_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "%(title).200B.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "prefer_ffmpeg": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "aac",
                "preferredquality": "192",
            },
            {"key": "FFmpegMetadata"},
        ],
        "postprocessor_args": ["-movflags", "+faststart"],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for q in queries:
            LOGGER.info("Searching for: %s", q)
            search_results = ydl.extract_info(f"ytsearch3:{q}", download=False)

            for entry in search_results.get("entries", []):
                title = entry.get("title", "").lower()
                uploader = (entry.get("uploader") or "").lower()
                url = entry.get("webpage_url")
                youtube_id = entry.get("id")

                if not url or not youtube_id:
                    continue

                if (
                    name.lower() in title and any(a.lower() in title for a in artists.split(","))
                ) or any(a.lower() in uploader for a in artists.split(",")):

                    LOGGER.info("Match found: %s (uploader: %s)", title, uploader)

                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    m4a_path = Path(filename).with_suffix(".m4a")

                    final_path = output_dir / f"{name}.m4a"
                    if m4a_path.exists():
                        m4a_path.rename(final_path)

                    trim_silence_m4a(final_path)

                    try:
                        # Tag metadata
                        audio = MP4(final_path)
                        audio["\xa9nam"] = name
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
                        LOGGER.error("Failed to tag metadata for %s: %s", name, e)

                    # ✅ Compute checksum
                    checksum = compute_checksum(final_path)

                    # ✅ Record to database
                    try:
                        record_download(
                            user_id=user_id,
                            track=track,
                            youtube_info=info,
                            filepath=str(final_path),
                            checksum=checksum,
                            spotify_id=spotify_id,
                        )
                        LOGGER.info("Recorded download for %s", name)
                    except Exception as e:
                        LOGGER.error("Failed to record DB entry for %s: %s", name, e)

                    return final_path

            LOGGER.info("No match found for query: %s", q)

    LOGGER.error("No suitable match found for track: %s - %s", artists, name)
    return None


def record_download(
    user_id: int,
    track: dict[str, str],
    youtube_info: dict,
    filepath: str,
    checksum: str,
    spotify_id: str,
):
    """Insert track and download info into the database using models.py helpers."""

    track_data = {
        "spotify_id": spotify_id,
        "name": track.get("name"),
        "artist": track.get("artists"),
        "album": track.get("album"),
        "year": track.get("album_release_year"),
        "duration_ms": track.get("duration_ms"),
    }

    track_id = models.get_or_create_track(track_data)

    # ✅ Update checksum after creation
    models.execute(
        "UPDATE tracks SET checksum=%s WHERE id=%s",
        (checksum, track_id),
    )

    youtube_id = youtube_info.get("id")
    filesize_bytes = youtube_info.get("filesize_approx") or youtube_info.get("filesize") or 0
    filesize_mb = round(filesize_bytes / 1_000_000, 2)

    models.execute(
        """
        INSERT INTO downloads (user_id, track_id, youtube_id, filepath, bitrate, filesize_mb)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (user_id, track_id, youtube_id, filepath, 192, filesize_mb),
    )

    # Log user history
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


# -----------------------------
# CLI ENTRY POINT
# -----------------------------
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
