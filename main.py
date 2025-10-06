import argparse
import logging
from pathlib import Path

# Import your internal modules
from backend.db import models
from backend.services import spotify_export, youtube_dl


def main() -> None:
    """Run Spotify export → YouTube download pipeline."""

    parser = argparse.ArgumentParser(
        description="Exports a Spotify playlist to CSV, then downloads all its songs via YouTube."
    )
    parser.add_argument(
        "--playlist",
        required=True,
        help="Spotify playlist URL or ID (used by spotify_export.py)."
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
        help="Number of parallel threads for YouTube downloads."
    )
    parser.add_argument(
        "--csv",
        default="exports/playlist.csv",
        help="Optional override path for exported CSV file."
    )

    args = parser.parse_args()

    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # --- Step 1: get or create user
    user_id = models.get_or_create_user(args.user)
    logging.info(f"User '{args.user}' (id={user_id}) ready")

    # --- Step 2: export playlist from Spotify
    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    logging.info("Exporting playlist from Spotify...")
    playlist_info, tracks = spotify_export.export_playlist(args.playlist, csv_path) # type: ignore
    logging.info(f"Exported playlist '{playlist_info['name']}' with {len(tracks)} track(s)")

    # --- Step 3: download tracks from YouTube
    logging.info("Starting YouTube downloads...")
    youtube_dl.process_tracks(tracks, args.output_directory, args.workers, user_id)
    logging.info("All done!")


if __name__ == "__main__":
    main()
