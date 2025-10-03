import os
import csv
import logging
from pathlib import Path
from typing import Any, Iterable, List, Optional, cast
from tempfile import NamedTemporaryFile

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
    """Trim leading and trailing silence from an M4A file using pydub and export as a proper MP4/M4A."""
    try:
        audio = AudioSegment.from_file(file_path)  # let ffmpeg detect container/codec

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
        if end_trim - start_trim < 500:  # avoid over-trim to nothing
            return file_path

        trimmed = audio[start_trim:end_trim]

        with NamedTemporaryFile(delete=False, suffix=".m4a") as tmp:
            tmp_path = Path(tmp.name)

        trimmed.export(
            tmp_path,
            format="mp4",              # MP4 container (correct for .m4a)
            codec="aac",
            bitrate="192k",
            parameters=["-movflags", "+faststart"]
        )

        tmp_path.replace(file_path)
        LOGGER.info("Trimmed & remuxed: %s", file_path)

    except Exception as e:
        LOGGER.error("Failed to trim silence for %s: %s", file_path, e)

    return file_path


# -----------------------------
# DOWNLOAD + TAGGING
# -----------------------------
def download_and_tag(track: dict[str, str], output_dir: Path) -> Optional[Path]:
    """Search YouTube for the track, download as M4A, trim silence, and tag with CSV metadata."""

    name = (track.get("name") or "").strip()
    artists = (track.get("artists") or "").strip()
    album = (track.get("album") or "").strip()
    year = (track.get("album_release_year") or "").strip()
    track_number = (track.get("track_number") or "").strip()
    disc_number = (track.get("disc_number") or "").strip()
    isrc = (track.get("isrc") or "").strip()
    added_at = (track.get("added_at") or "").strip()
    playlist_index = (track.get("playlist_index") or "").strip()

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
            {
                "key": "FFmpegMetadata",
            },
        ],
        "postprocessor_args": ["-movflags", "+faststart"],
    }

    with yt_dlp.YoutubeDL(cast(dict[str, Any], ydl_opts)) as ydl:
        for q in queries:
            LOGGER.info("Searching for: %s", q)
            search_results = ydl.extract_info(f"ytsearch5:{q}", download=False)

            for entry in search_results.get("entries", []):
                title = entry.get("title", "").lower()
                uploader = (entry.get("uploader") or "").lower()
                url = entry.get("webpage_url")
                if not url:
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
                        audio = MP4(final_path)
                        audio["\xa9nam"] = name
                        audio["\xa9ART"] = artists
                        if album:
                            audio["\xa9alb"] = album
                        if year:
                            audio["\xa9day"] = year
                        if track_number and not (album.lower() == name.lower() and track_number == "1"):
                            audio["trkn"] = [(int(track_number), 0)]
                        if disc_number:
                            audio["disk"] = [(int(disc_number), 0)]
                        if isrc:
                            audio["----:com.apple.iTunes:ISRC"] = [isrc.encode("utf-8")]
                        if added_at:
                            audio["\xa9cmt"] = added_at
                        if playlist_index:
                            # ✅ custom freeform tag for playlist order
                            audio["----:com.apple.iTunes:PlaylistIndex"] = [playlist_index.encode("utf-8")]

                        audio.save()
                        LOGGER.info("Tagged metadata for %s", name)
                    except Exception as e:
                        LOGGER.error("Failed to tag metadata for %s: %s", name, e)

                    return final_path

            LOGGER.info("No match found for query: %s", q)

    LOGGER.error("No suitable match found for track: %s - %s", artists, name)
    return None


# -----------------------------
# PROCESS ALL TRACKS
# -----------------------------
def process_tracks(tracks: Iterable[dict[str, str]], output_directory: str) -> List[Path]:
    output_dir = Path(output_directory)
    downloaded: List[Path] = []

    for track in tracks:
        try:
            result = download_and_tag(track, output_dir)
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
# CLI
# -----------------------------
def build_argument_parser():
    import argparse
    parser = argparse.ArgumentParser(description="Download audio tracks as M4A files with metadata and silence trimming")
    parser.add_argument("--csv", required=True, help="Path to Spotify-exported CSV (from playlist_exporter.py).")
    parser.add_argument("-o", "--output-directory", default="downloads", help="Output directory for M4A files.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    tracks = load_tracks_from_csv(args.csv)
    process_tracks(tracks, args.output_directory)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
