import re
import json
import subprocess
import logging
import unicodedata
from typing import Optional, Tuple
import yt_dlp
from backend.db import models

LOGGER = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# TEXT NORMALIZATION
# ─────────────────────────────────────────────

def normalize_name(s: str) -> str:
    """Normalize artist/channel names for loose comparison."""
    s = unicodedata.normalize("NFKC", s.lower())
    s = re.sub(r"(?i)\b(vevo|topic|official|music|channel)\b", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s.strip()

# ─────────────────────────────────────────────
# CHANNEL CACHE (DB-INTEGRATED)
# ─────────────────────────────────────────────

def find_or_cache_artist_channel(artist: str) -> str:
    """Get a YouTube channel for an artist, caching in DB if not found."""
    cached = models.get_artist_channel(artist)
    if cached:
        return cached

    cmd = [
        "yt-dlp",
        f"ytsearch5:{artist} official channel",
        "--flat-playlist",
        "--dump-json",
        "--quiet",
    ]
    try:
        output = subprocess.check_output(cmd, text=True, errors="ignore")
    except subprocess.CalledProcessError:
        LOGGER.warning("Failed to search channel for %s", artist)
        return ""

    artist_norm = normalize_name(artist)
    best_match_url = ""
    best_similarity = 0.0

    for line in output.splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        if "channel" not in data.get("url", ""):
            continue

        title = data.get("title", "")
        title_norm = normalize_name(title)
        matches = sum(a == b for a, b in zip(artist_norm, title_norm))
        similarity = matches / max(len(artist_norm), len(title_norm), 1)

        if similarity > best_similarity:
            best_similarity = similarity
            best_match_url = data["url"]

    if best_match_url:
        models.set_artist_channel(artist, best_match_url)
        LOGGER.info("✅ Cached channel for %s (%.2f match): %s", artist, best_similarity, best_match_url)
        return best_match_url

    LOGGER.warning("⚠️ No good channel found for %s", artist)
    return ""

# ─────────────────────────────────────────────
# YOUTUBE SEARCH
# ─────────────────────────────────────────────

def search_youtube_for_song(name: str, artist: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Search YouTube for a song and return (best_url, channel_url).
    Prioritizes lyric and official audio videos.
    Ignores music videos.
    """
    if not name:
        return None, None

    channel_url = find_or_cache_artist_channel(artist)
    base_queries = [
        f"{name} {artist} lyrics",
        f"{name} {artist} official audio",
        f"{name} {artist}"
    ]
    queries = []
    if channel_url:
        queries.extend([f"{channel_url} {q}" for q in base_queries])
    queries.extend(base_queries)

    ignore_phrases = ("music video", "official video")
    prefer_phrases = ("lyric", "official audio")

    best_url = None
    best_score = -1

    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        for q in queries:
            try:
                info = ydl.extract_info(f"ytsearch10:{q}", download=False)
            except Exception as e:
                LOGGER.warning("Search failed for %s: %s", q, e)
                continue

            for entry in info.get("entries", []):
                title = entry.get("title", "").lower()
                desc = entry.get("description", "").lower()
                uploader = entry.get("uploader", "")
                url = entry.get("webpage_url")

                if not url:
                    continue

                # Skip music videos
                if any(phrase in title or phrase in desc for phrase in ignore_phrases):
                    continue

                # Assign score based on priority keywords
                score = 0
                if any(p in title for p in prefer_phrases):
                    score += 3
                if artist.lower() in title:
                    score += 2
                if name.lower() in title:
                    score += 2

                if score > best_score:
                    best_score = score
                    best_url = url

            if best_url:
                break  # stop once we find a high-confidence match

    if best_url:
        LOGGER.info("🎵 Found match for %s - %s → %s", artist, name, best_url)
    else:
        LOGGER.warning("❌ No good match for %s - %s", artist, name)

    return best_url, channel_url
