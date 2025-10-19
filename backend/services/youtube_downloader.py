# youtube_dl.py (simplified rewrite)
import re, logging, subprocess, json, time, unicodedata, hashlib
from pathlib import Path
from mutagen.mp4 import MP4
from pydub import AudioSegment, silence
import yt_dlp
from typing import Any, Optional
from backend.db import models
from backend.services.youtube_searcher import find_or_cache_artist_channel, search_youtube_for_song

LOGGER = logging.getLogger(__name__)

def normalize_text(s: str) -> str:
    return unicodedata.normalize("NFKC", s.lower()).strip()

def trim_silence_m4a(file_path: Path) -> Path:
    """Trim leading and trailing silence from an M4A file."""
    try:
        audio = AudioSegment.from_file(file_path)
        nonsilent = silence.detect_nonsilent(audio, min_silence_len=500, silence_thresh=audio.dBFS - 40)
        if not nonsilent:
            return file_path
        start, end = nonsilent[0][0], nonsilent[-1][1]
        trimmed = audio[start:end]
        trimmed.export(file_path, format="mp4", codec="aac", bitrate="192k")
        LOGGER.info("Trimmed silence: %s", file_path)
    except Exception as e:
        LOGGER.warning("Silence trim failed for %s: %s", file_path, e)
    return file_path

def compute_checksum(file_path: Path) -> str:
    """Compute SHA256 checksum for a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def normalize_artist_name(name: str) -> str:
    """Normalize artist/channel names for loose comparison."""
    s = re.sub(r"(?i)\b(vevo|topic|official|music|channel)\b", "", name)
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return s

def search_best_youtube(track: dict) -> str | None:
    name = track.get("name","").strip()
    artist = track.get("artist","").strip()
    if not name: return None
    chan = find_or_cache_artist_channel(artist)
    queries = [f"{name} {artist} lyrics", f"{name} {artist} official audio", f"{name} {artist}"]
    banned = ("music video", "official video")
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        for q in queries:
            if chan: q = f"{chan} {q}"
            try:
                info = ydl.extract_info(f"ytsearch5:{q}", download=False)
            except Exception as e:
                LOGGER.warning("Search fail %s: %s", q, e)
                continue
            for e in info.get("entries", []):
                title = normalize_text(e.get("title",""))
                desc = normalize_text(e.get("description",""))
                if any(b in title or b in desc for b in banned): continue
                if all(w in title for w in name.lower().split()):
                    return e["webpage_url"]
    return None

def download_and_tag(track: dict[str, Any], output_dir: Path, user_id: int) -> Optional[Path]:
    """
    Find, download, and tag a song from YouTube using cached artist channels when possible.
    Prefers lyric / official audio videos, ignores music videos.
    """

    name = (track.get("name") or "").strip()
    artist = (track.get("artist") or "").strip()
    album = (track.get("album") or "").strip()
    year = (track.get("year") or "").strip()
    spotify_id = (track.get("spotify_id") or "").strip()

    if not name:
        LOGGER.warning("Skipping track with missing name: %s", track)
        return None

    # Step 1: Get best YouTube match
    youtube_url, channel_url = search_youtube_for_song(name, artist)
    if not youtube_url:
        LOGGER.warning("❌ No suitable YouTube match for %s - %s", artist, name)
        return None

    # Step 2: Download
    output_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "%(title).200B.%(ext)s"),
        "quiet": True,
        "noplaylist": True,
        "prefer_ffmpeg": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "aac", "preferredquality": "192"},
            {"key": "FFmpegMetadata"},
        ],
        "postprocessor_args": ["-movflags", "+faststart"],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(youtube_url, download=True)
        except Exception as e:
            LOGGER.error("Download failed for %s: %s", youtube_url, e)
            return None

        filename = ydl.prepare_filename(info)
        m4a_path = Path(filename).with_suffix(".m4a")

    # Step 3: Tagging
    trim_silence_m4a(m4a_path)

    try:
        audio = MP4(m4a_path)
        audio["\xa9nam"] = name
        if artist:
            audio["\xa9ART"] = artist
        if album:
            audio["\xa9alb"] = album
        if year:
            audio["\xa9day"] = year
        audio.save()
        LOGGER.info("Tagged metadata for %s", name)
    except Exception as e:
        LOGGER.warning("Metadata tagging failed for %s: %s", name, e)

    # Step 4: Database record
    checksum = compute_checksum(m4a_path)
    try:
        models.record_download(
            user_id=user_id,
            track=track,
            youtube_info=info,
            filepath=str(m4a_path),
            checksum=checksum,
            spotify_id=spotify_id,
        )
        LOGGER.info("Recorded download for %s - %s", artist, name)
    except Exception as e:
        LOGGER.error("Failed to record DB entry for %s: %s", name, e)

    return m4a_path
