"""
download_lyric_videos.py
Scan a folder of mp3s => for each song, search YouTube for lyric videos => download audio as mp3.

Usage:
    python download_lyric_videos.py --input /path/to/mp3s --output /path/to/output --num_search 5

Notes:
 - Only use for songs you own/have rights to.
 - Requires: yt-dlp, mutagen, ffmpeg.
"""

import os
import re
import time
import argparse
from pathlib import Path
from typing import Optional, Dict, Any, List
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
import yt_dlp

# Heuristic keywords for lyric videos
LYRIC_KEYWORDS = ("lyric", "lyrics", "lyric video")

def read_metadata(filepath: Path) -> Dict[str, Optional[str]]:
    """Try reading ID3 tags (artist, title). Fall back to filename parsing."""
    result = {"artist": None, "title": None}
    try:
        tags = EasyID3(str(filepath))
        result["artist"] = tags.get("artist", [None])[0]
        result["title"] = tags.get("title", [None])[0]
    except ID3NoHeaderError:
        pass
    # If missing, try to parse filename like "Artist - Title.mp3" or "Title.mp3"
    if not result["title"]:
        name = filepath.stem
        if " - " in name:
            parts = name.split(" - ", 1)
            result["artist"] = result["artist"] or parts[0].strip()
            result["title"] = parts[1].strip()
        else:
            result["title"] = name.strip()
    return result

def choose_best_entry(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the best search result that looks like a lyric video."""
    if not entries:
        return None

    def score_entry(e: Dict[str, Any]) -> float:
        title = (e.get("title") or "").lower()
        score = 0.0
        if any(k in title for k in LYRIC_KEYWORDS):
            score += 50.0
        views = e.get("view_count") or 0
        score += min(views / 1_000_000.0, 20.0)
        channel = (e.get("uploader") or "").lower()
        if "official" in title or "official" in channel:
            score += 5.0
        return score

    return max(entries, key=score_entry, default=None)

def build_search_query(artist: Optional[str], title: Optional[str]) -> str:
    """Create a YouTube search query that prefers lyric videos."""
    if artist:
        base = f"{artist} - {title}"
    else:
        base = title or ""
    return f"{base} lyric video"

def download_audio_from_url(url: str, out_file: Path, verbose: bool=False) -> bool:
    """Use yt-dlp to download best audio and convert to mp3 with ffmpeg."""
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_file),  # exact filename
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
            {"key": "FFmpegMetadata"}
        ],
        "overwrites": False,   # do not overwrite existing
        "quiet": not verbose,
        "no_warnings": True,
        "noplaylist": True,
        "ignoreerrors": False,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        print(f"[ERROR] Download failed for {url}: {e}")
        return False

def search_youtube(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Search YouTube and return entries metadata without downloading."""
    search_str = f"ytsearch{max_results}:{query}"
    ydl_opts = {"quiet": True, "skip_download": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(search_str, download=False)
            return [e for e in (info.get("entries") or []) if e]
        except Exception as e:
            print(f"[ERROR] Search failed for query: {query} -> {e}")
            return []

def safe_filename(s: str) -> str:
    """Remove characters not suitable in filenames."""
    return re.sub(r'[\\/*?:"<>|]', "", s)

def main(input_dir: Path, output_dir: Path, num_search: int, verbose: bool=False, wait_between: float=1.0):
    input_dir = input_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    mp3_files = list(input_dir.glob("*.mp3")) + list(input_dir.glob("*.MP3"))
    print(f"Found {len(mp3_files)} mp3 files in {input_dir}")

    for mp3 in mp3_files:
        meta = read_metadata(mp3)
        artist, title = meta.get("artist"), meta.get("title")
        print(f"\nProcessing: {mp3.name}  -> Artist: '{artist}', Title: '{title}'")
        query = build_search_query(artist, title)
        print(f"Searching YouTube for: {query!r} (top {num_search})")
        entries = search_youtube(query, max_results=num_search)
        if not entries:
            print("  No search results, skipping.")
            continue

        best = choose_best_entry(entries)
        if not best:
            print("  Couldn't pick a best result, skipping.")
            continue

        best_title = best.get("title", "unknown")
        best_url = best.get("webpage_url") or best.get("url")
        views = best.get("view_count", 0)
        print(f"  Selected: {best_title} ({views} views) -> {best_url}")

        # enforce consistent output filename
        wanted_name = safe_filename(f"{artist or 'unknown'} - {title or best_title}")
        expected_file = output_dir / f"{wanted_name}.mp3"

        if expected_file.exists():
            print(f"  ✅ Already exists: {expected_file}, skipping.")
        else:
            success = download_audio_from_url(best_url, expected_file, verbose=verbose)
            if success:
                print(f"   Downloaded: {expected_file}")
            else:
                print(f"   Failed to download: {best_url}")

        if wait_between > 0:
            time.sleep(wait_between)

if __name__ == "__main__":
    # Hardcoded paths for Windows
    input_dir = Path(r"C:\\Users\\letsbenameless\\Desktop\\Audio Devices\\songs\\duds")
    output_dir = Path(r"C:\\Users\\letsbenameless\\Desktop\\Audio Devices\\songs\\dud-replacements")

    output_dir.mkdir(parents=True, exist_ok=True)

    main(input_dir, output_dir, num_search=5, verbose=True, wait_between=1.0)