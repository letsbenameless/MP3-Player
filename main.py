import argparse
import logging
from pathlib import Path
import csv

# Internal modules
from backend.db import models
from backend.services import spotify_single_export, youtube_downloader

LOGGER = logging.getLogger(__name__)

def main() -> None:
    """Run Spotify export → YouTube link search → batch download pipeline, or YouTube-only mode."""

    parser = argparse.ArgumentParser(
        description=(
            "Exports a Spotify playlist to CSV, searches YouTube for lyric videos or official uploads, "
            "and downloads all matched songs as M4A files. Can also download directly from YouTube links."
        )
    )
    parser.add_argument(
        "--playlist",
        help="Spotify playlist URL or ID (used by spotify_export.py)."
    )
    parser.add_argument(
        "--youtube",
        nargs="+",
        help="One or more YouTube links or search queries. Example: "
             "'https://www.youtube.com/watch?v=dQw4w9WgXcQ' or multiple links separated by space."
    )
    parser.add_argument(
        "--user",
        required=True,
        help="Username for linking downloads to this user in the database."
    )
    parser.add_argument(
        "-o", "--output-directory",
        default="downloads",
        help="Output directory for downloaded M4A files."
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=4,
        help="Number of parallel threads for YouTube downloads (Spotify playlists only)."
    )
    parser.add_argument(
        "--csv",
        default="exports/playlist.csv",
        help="Optional override path for exported CSV file."
    )
    parser.add_argument(
        "--skip-search",
        action="store_true",
        help="Skip YouTube searching if playlist CSV already has youtube_url links."
    )

    args = parser.parse_args()

    # Setup logging
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # --- Ensure user exists ---
    user_id = models.get_or_create_user(args.user)
    LOGGER.info(f"User '{args.user}' (id={user_id}) ready")

    # --- YouTube direct mode ---
    if args.youtube:
        LOGGER.info("Running in YouTube direct-download mode...")
        youtube_downloader.fast_youtube_download(
            urls=args.youtube,
            output_dir=args.output_directory,
            username=args.user,
            trim=False
        )
        LOGGER.info("✅ All direct YouTube downloads complete.")
        return

    # --- Spotify playlist mode ---
    if not args.playlist:
        parser.error("You must specify either --playlist (Spotify) or --youtube (YouTube-only mode).")

    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    LOGGER.info("Exporting playlist from Spotify...")
    playlist_info, tracks = spotify_single_export.export_playlist(args.playlist, csv_path)  # type: ignore
    LOGGER.info(f"Exported playlist '{playlist_info['name']}' with {len(tracks)} track(s).")

    # --- YouTube link enrichment ---
    if not args.skip_search:
        LOGGER.info("Searching YouTube for all tracks (lyrics prioritized)...")
        results = youtube_downloader.search_youtube_links(tracks)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[*tracks[0].keys(), "youtube_url"])
            writer.writeheader()
            for t in tracks:
                found = next((r for r in results if r["spotify_id"] == t["spotify_id"]), None)
                t["youtube_url"] = found["youtube_url"] if found else ""
                writer.writerow(t)
        LOGGER.info("✅ Playlist CSV updated with YouTube links.")
    else:
        LOGGER.info("Skipping YouTube search — using existing youtube_url entries.")

    # --- Collect all matched URLs ---
    urls = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("youtube_url"):
                urls.append(row["youtube_url"])

    if not urls:
        LOGGER.warning("No valid YouTube URLs found — nothing to download.")
        return

    # --- Download all URLs ---
    LOGGER.info("Starting YouTube downloads for %d tracks...", len(urls))
    youtube_downloader.fast_youtube_download(
        urls=urls,
        output_dir=args.output_directory,
        username=args.user,
        trim=False
    )
    LOGGER.info("✅ All done! Downloads saved to %s", args.output_directory)


if __name__ == "__main__":
    main()
