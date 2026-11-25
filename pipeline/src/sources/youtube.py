from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

YOUTUBE_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "youtube_urls.json"


def extract_video_id(url_or_id: str) -> Optional[str]:
    s = (url_or_id or "").strip()
    if not s:
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/|/live/)([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, s)
        if m:
            return m.group(1)
    return None


def load_configured_youtube_urls(date_str: str, meeting_slug: str, firestore_config: Optional[Dict] = None) -> List[str]:
    urls: List[str] = []
    if YOUTUBE_CONFIG_PATH.exists():
        try:
            cfg = json.loads(YOUTUBE_CONFIG_PATH.read_text(encoding="utf-8"))
            day_map = (cfg.get("meetings") or {}).get(date_str) or {}
            urls.extend(day_map.get(meeting_slug, []))
            urls.extend(day_map.get("all", []))
        except Exception as exc:
            logger.warning("Failed reading youtube_urls.json: %s", exc)

    if firestore_config:
        day_map = (firestore_config.get("meetings") or {}).get(date_str) or {}
        urls.extend(day_map.get(meeting_slug, []))
        urls.extend(day_map.get("all", []))

    # Deduplicate preserving order
    seen = set()
    unique: List[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def fetch_transcripts_for_urls(urls: List[str], max_chars_per_video: int = 8000) -> str:
    """Fetch English YouTube preview transcripts via youtube-transcript-api, gracefully handling errors."""
    if not urls:
        return ""

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        logger.warning("youtube-transcript-api not installed; skipping YouTube transcripts.")
        return ""

    chunks: List[str] = []
    for url in urls:
        vid = extract_video_id(url)
        if not vid:
            continue
        try:
            # Support both newer (>=1.x instance .fetch) and older (0.6.x static .get_transcript) APIs
            if hasattr(YouTubeTranscriptApi, "get_transcript"):
                segments = YouTubeTranscriptApi.get_transcript(vid, languages=["en", "en-AU", "en-GB"])
                text = " ".join(seg.get("text", "") for seg in segments)
            else:
                api = YouTubeTranscriptApi()
                fetched = api.fetch(vid, languages=["en", "en-AU", "en-GB"])
                text = " ".join(getattr(seg, "text", "") for seg in fetched)
            cleaned = re.sub(r"\s+", " ", text).strip()[:max_chars_per_video]
            if cleaned:
                chunks.append(f"[Video {vid}]: {cleaned}")
        except Exception as exc:
            logger.warning("Could not fetch transcript for YouTube %s: %s", vid, exc)

    return "\n\n".join(chunks)
