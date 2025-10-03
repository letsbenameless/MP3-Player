import os
import acoustid
import chromaprint
import argparse
import csv
import logging
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, List, Optional, cast

import yt_dlp

LOGGER = logging.getLogger(__name__)
ACOUSTID_API_KEY = os.getenv("ACOUSTID_API_KEY", "")


def similar(a: str, b: str) -> float:
    """Return a similarity ratio between two strings."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def identify_file(filepath: Path) -> Optional[dict[str, Any]]:
    """Generate a Chromaprint fingerprint and query AcoustID for metadata."""
    try:
        duration, fp = chromaprint.encode_file(str(filepath))  # ✅ use acoustid wrapper
        results = acoustid.lookup(
            ACOUSTID_API_KEY, fp, duration, meta="recordings sources"
        )
        for result in results["results"]:
            if "recordings" in result:
                rec = result["recordings"][0]
                title = rec.get("title", "")
                artist = rec["artists"][0]["name"] if rec.get("artists") else ""
                return {"title": title, "artist": artist, "score": result["score"]}
    except Exception as e:
        LOGGER.error("AcoustID lookup failed: %s", e)
    return None


# -----------------------------
# CSV LOADER
# -----------------------------
def load_tracks_from_csv(csv_path: str) -> List[str]:
    """Read Spotify CSV and return search queries for YouTube."""
    queries: List[str] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row is None:
                continue
            name = (row.get("name") or "").strip()
            artists = (row.get("artists") or "").strip()
            if name and artists:
                queries.append(f"{name} {artists} lyrics")
            elif name:
                queries.append(name)
    return queries


# -----------------------------
# DOWNLOAD FUNCTIONS
# -----------------------------
def download_from_search(
    query: str, output_dir: Path, expected_title: str = "", expected_artist: str = ""
) -> Optional[Path]:
    """Search YouTube for a track, fingerprint with AcoustID, and download only if it matches Spotify metadata."""

    output_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Searching for: %s", query)

    ydl_opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "opus",
                "preferredquality": "160",
            }
        ],
    }

    with yt_dlp.YoutubeDL(cast(dict[str, Any], ydl_opts)) as ydl:  # type: ignore[arg-type]
        # Search top 5 candidates
        search_results = ydl.extract_info(f"ytsearch5:{query}", download=False)

        for entry in search_results.get("entries", []):
            url = entry.get("webpage_url")
            if not url:
                continue

            LOGGER.info("Trying candidate: %s", url)

            # Download this candidate into temp file
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            opus_path = Path(filename).with_suffix(".opus")

            # Run AcoustID fingerprint
            meta = identify_file(opus_path)
            if meta:
                sim_title = similar(meta["title"], expected_title)
                sim_artist = similar(meta["artist"], expected_artist)
                LOGGER.info(
                    "Candidate fingerprinted: %s - %s (score=%.2f, sim_title=%.2f, sim_artist=%.2f)",
                    meta["artist"],
                    meta["title"],
                    meta["score"],
                    sim_title,
                    sim_artist,
                )

                # Accept if similarity high enough
                if sim_title > 0.6 and sim_artist > 0.5:
                    LOGGER.info("Accepted track: %s -> %s", query, opus_path)
                    return opus_path
                else:
                    LOGGER.warning(
                        "Rejected candidate (metadata mismatch): %s", opus_path
                    )
                    opus_path.unlink(missing_ok=True)  # delete file
            else:
                LOGGER.warning(
                    "Rejected candidate (no AcoustID match): %s", opus_path
                )
                opus_path.unlink(missing_ok=True)

    LOGGER.error("No suitable match found for query: %s", query)
    return None


def process_queries(queries: Iterable[str], output_directory: str) -> List[Path]:
    """Download all provided search queries into output_directory as Opus files."""

    output_dir = Path(output_directory)
    downloaded: List[Path] = []

    for query in queries:
        try:
            result = download_from_search(query, output_dir)
            if result is not None:  # ✅ only append non-None
                downloaded.append(result)
        except Exception as exc:  # defensive logging
            LOGGER.exception("Failed to download %s: %s", query, exc)

    if downloaded:
        LOGGER.info(
            "Saved %d track(s) to %s as .opus files", len(downloaded), output_dir
        )
    else:
        LOGGER.warning("No tracks were downloaded into %s", output_dir)

    return downloaded


# -----------------------------
# CLI
# -----------------------------
def build_argument_parser() -> argparse.ArgumentParser:
    """Create an argument parser for the downloader CLI."""

    parser = argparse.ArgumentParser(description="Download audio tracks as Opus files")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--csv",
        help="Path to Spotify-exported CSV (from playlist_exporter.py).",
    )
    group.add_argument(
        "urls",
        nargs="*",
        help="One or more YouTube URLs to download directly.",
    )

    parser.add_argument(
        "-o",
        "--output-directory",
        default="downloads",
        help=(
            "Directory where downloaded tracks will be stored as .opus files. "
            "The directory will be created if it does not already exist."
        ),
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    # Basic logging setup if not configured by the host app
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.csv:
        queries = load_tracks_from_csv(args.csv)
        process_queries(queries, args.output_directory)
    else:
        process_queries(args.urls, args.output_directory)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
