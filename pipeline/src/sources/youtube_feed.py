from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pipeline.src.http_client import PoliteHttpClient
from pipeline.src.sources.youtube import fetch_transcripts_for_urls

logger = logging.getLogger(__name__)

CHANNELS_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "youtube_channels.json"
ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

_BOILERPLATE_LINE_RE = re.compile(
    r"^(?:"
    r"(?:✅|📲|🔗|👉)?\s*(?:subscribe|follow us|stay up to date|like .+ on facebook|"
    r"visit .+|check out .+|stream .+|instagram:|tiktok:|facebook:|twitter:|x:|spotify:|apple podcasts:|"
    r"think\.\s*is this a bet|for free and confidential support|gamble responsibly|chances are you)"
    r"|https?://\S+$"
    r")",
    re.IGNORECASE,
)


def clean_media_description(raw: str, max_chars: int = 2400) -> str:
    """Clean YouTube <media:description> by dropping boilerplate social/RG footers while keeping tips & timestamps."""
    if not raw:
        return ""
    kept: List[str] = []
    for line in raw.splitlines():
        s = re.sub(r"\s+", " ", line).strip()
        if not s:
            continue
        if _BOILERPLATE_LINE_RE.search(s):
            continue
        # Strip trailing RG blurb appended inline
        s = re.sub(r"\s*THINK\.\s*IS THIS A BET.*$", "", s, flags=re.IGNORECASE).strip()
        if s:
            kept.append(s)
    cleaned = " | ".join(kept)
    return cleaned[:max_chars]


def build_video_metadata_text(video: Dict[str, Any]) -> str:
    """Compose a concise grounded snippet from YouTube Atom XML <title> and <media:description>."""
    channel = (video.get("channel") or "YouTube").strip()
    title = (video.get("title") or "").strip()
    desc = (video.get("description") or "").strip()
    if desc:
        return f"[YouTube {channel} | {title}]: {desc}"
    if title:
        return f"[YouTube {channel} | {title}]"
    return ""


def load_youtube_channels_config() -> Dict[str, Any]:
    if not CHANNELS_CONFIG_PATH.exists():
        return {"lookback_hours": 36, "max_videos_per_meeting": 3, "channels": []}
    return json.loads(CHANNELS_CONFIG_PATH.read_text(encoding="utf-8"))


def parse_youtube_atom_feed(
    xml_text: str,
    channel_name: str,
    now_utc: Optional[datetime] = None,
    lookback_hours: float = 36.0,
    upper_bound_utc: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Parse YouTube public Atom XML feed and return entries published within lookback_hours.
    When upper_bound_utc is supplied (e.g., blind historical replay), strictly excludes any
    video published at or after upper_bound_utc so post-race wraps cannot leak results.
    """
    if not xml_text or not xml_text.strip():
        return []

    anchor_utc = upper_bound_utc or now_utc or datetime.now(timezone.utc)
    cutoff = anchor_utc - timedelta(hours=lookback_hours)

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("Failed parsing YouTube XML feed for %s: %s", channel_name, exc)
        return []

    videos: List[Dict[str, Any]] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        vid_el = entry.find("yt:videoId", ATOM_NS)
        title_el = entry.find("atom:title", ATOM_NS)
        pub_el = entry.find("atom:published", ATOM_NS)
        desc_el = entry.find("media:group/media:description", ATOM_NS)
        if desc_el is None:
            desc_el = entry.find("media:description", ATOM_NS)

        video_id = (vid_el.text or "").strip() if vid_el is not None else ""
        title = (title_el.text or "").strip() if title_el is not None else ""
        pub_raw = (pub_el.text or "").strip() if pub_el is not None else ""
        raw_description = (desc_el.text or "").strip() if desc_el is not None else ""
        description = clean_media_description(raw_description)
        if not video_id or not title:
            continue

        published_dt = None
        if pub_raw:
            try:
                published_dt = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                if published_dt.tzinfo is None:
                    published_dt = published_dt.replace(tzinfo=timezone.utc)
                published_dt = published_dt.astimezone(timezone.utc)
            except Exception:
                published_dt = None

        if published_dt is not None and published_dt < cutoff:
            continue
        if upper_bound_utc is not None:
            if published_dt is None or published_dt >= upper_bound_utc:
                continue

        videos.append(
            {
                "channel": channel_name,
                "video_id": video_id,
                "title": title,
                "description": description,
                "raw_description": raw_description[:2400],
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "published_at": published_dt.isoformat() if published_dt else pub_raw,
            }
        )
    return videos


def match_videos_to_meeting(
    videos: List[Dict[str, Any]],
    meeting_name: str,
    aliases: Optional[List[str]] = None,
    max_videos: int = 3,
) -> List[Dict[str, Any]]:
    """Match feed videos whose title or <media:description> contains the meeting name or any venue alias."""
    terms = []
    for raw in [meeting_name] + list(aliases or []):
        cleaned = (raw or "").strip()
        if cleaned and len(cleaned) >= 4:
            terms.append(cleaned)
    # Deduplicate longest-first
    unique_terms = sorted(set(terms), key=len, reverse=True)
    if not unique_terms:
        return []

    patterns = [re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE) for t in unique_terms]
    matched: List[Dict[str, Any]] = []
    seen_ids = set()
    for v in videos:
        vid = v["video_id"]
        if vid in seen_ids:
            continue
        searchable = f"{v.get('title') or ''}\n{v.get('description') or ''}"
        if any(p.search(searchable) for p in patterns):
            matched.append(v)
            seen_ids.add(vid)
            if len(matched) >= max_videos:
                break
    return matched


def extract_runner_relevant_snippets(
    transcript_text: str,
    runner_names: List[str],
    max_total_chars: int = 4800,
    window_chars: int = 240,
) -> str:
    """
    Extract focused snippets around mentioned runner names and 'Race N' cues so
    meeting-level Gemini prompts get high-signal YouTube preview context with zero extra calls.
    """
    text = (transcript_text or "").strip()
    if not text:
        return ""
    if len(text) <= max_total_chars:
        return text

    spans: List[Tuple[int, int]] = []
    lower_text = text.lower()

    for name in runner_names:
        n_clean = (name or "").strip().lower()
        if len(n_clean) < 4:
            continue
        start = 0
        hits = 0
        while hits < 2:
            idx = lower_text.find(n_clean, start)
            if idx == -1:
                break
            spans.append((max(0, idx - window_chars // 2), min(len(text), idx + len(n_clean) + window_chars)))
            start = idx + len(n_clean)
            hits += 1

    # Also capture Race 1..Race 10 headings if runner names didn't match enough
    if len(spans) < 4:
        for m in re.finditer(r"\brace\s+(?:[1-9]|10)\b", lower_text):
            spans.append((max(0, m.start() - 60), min(len(text), m.end() + window_chars)))
            if len(spans) >= 12:
                break

    if not spans:
        return text[:max_total_chars]

    # Merge overlapping spans
    spans.sort()
    merged: List[Tuple[int, int]] = []
    for s, e in spans:
        if not merged or s > merged[-1][1] + 20:
            merged.append((s, e))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))

    chunks = [text[s:e].strip() for s, e in merged]
    combined = " ... ".join(chunks)
    return combined[:max_total_chars]


def match_general_preview_videos(
    videos: List[Dict[str, Any]],
    keywords: List[str],
    exclude_ids: Optional[set] = None,
    max_candidates: int = 4,
) -> List[Dict[str, Any]]:
    """Find general daily preview videos (Best Bets, Racing Preview, Midweek, etc.) via title or <media:description>."""
    exclude_ids = exclude_ids or set()
    kw_lower = [k.lower().strip() for k in keywords if k.strip()]
    out: List[Dict[str, Any]] = []
    seen = set(exclude_ids)
    for v in videos:
        vid = v["video_id"]
        if vid in seen:
            continue
        searchable = f"{v.get('title') or ''}\n{v.get('description') or ''}".lower()
        if any(kw in searchable for kw in kw_lower):
            out.append(v)
            seen.add(vid)
            if len(out) >= max_candidates:
                break
    return out


def count_runner_mentions(transcript_text: str, runner_names: List[str]) -> int:
    if not transcript_text or not runner_names:
        return 0
    tl = transcript_text.lower()
    hits = 0
    seen = set()
    for name in runner_names:
        clean = (name or "").strip().lower()
        if len(clean) >= 4 and clean not in seen:
            seen.add(clean)
            if clean in tl:
                hits += 1
    return hits


class YouTubeFeedDiscovery:
    """Zero-key public YouTube Atom XML feed discovery for Australian racing preview videos."""

    def __init__(self, http: PoliteHttpClient) -> None:
        self.http = http
        self.cfg = load_youtube_channels_config()
        self._transcript_cache: Dict[str, str] = {}

    def _get_cached_transcript(self, url: str) -> str:
        if url not in self._transcript_cache:
            try:
                self._transcript_cache[url] = fetch_transcripts_for_urls([url], max_chars_per_video=8000)
            except Exception as exc:
                logger.warning("YouTube caption fetch failed for %s (continuing with XML metadata): %s", url, exc)
                self._transcript_cache[url] = ""
        return self._transcript_cache[url]

    def fetch_recent_channel_videos(
        self,
        now_utc: Optional[datetime] = None,
        lookback_hours: Optional[float] = None,
        upper_bound_utc: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        channels = self.cfg.get("channels") or []
        lookback = float(lookback_hours if lookback_hours is not None else self.cfg.get("lookback_hours", 36))
        all_videos: List[Dict[str, Any]] = []

        for ch in channels:
            cid = (ch.get("channel_id") or "").strip()
            cname = (ch.get("name") or cid).strip()
            if not cid:
                continue
            feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
            try:
                xml_text = self.http.get_text(
                    feed_url,
                    headers={"Accept": "application/atom+xml, application/xml, text/xml, */*"},
                    allow_404=True,
                )
                if xml_text:
                    vids = parse_youtube_atom_feed(
                        xml_text,
                        cname,
                        now_utc=now_utc,
                        lookback_hours=lookback,
                        upper_bound_utc=upper_bound_utc,
                    )
                    all_videos.extend(vids)
                    logger.info("YouTube XML feed [%s]: %d video(s) within %.0fh window", cname, len(vids), lookback)
            except Exception as exc:
                logger.warning("Could not fetch YouTube XML feed for %s (%s): %s", cname, cid, exc)

        return all_videos

    def fetch_meeting_transcript(
        self,
        recent_videos: List[Dict[str, Any]],
        meeting_name: str,
        aliases: Optional[List[str]] = None,
        extra_urls: Optional[List[str]] = None,
        runner_names: Optional[List[str]] = None,
    ) -> Tuple[List[str], str]:
        """
        Match venue-specific titles/descriptions AND general daily preview videos whose
        <media:description> or captions mention >=2 runners racing at this meeting, plus any manual URLs.
        Always uses Atom XML <title> + <media:description> (100% immune to Azure/cloud caption IP blocks),
        and appends youtube-transcript-api captions as a graceful best-effort bonus.
        Returns (urls_used, focused_text).
        """
        max_vids = int(self.cfg.get("max_videos_per_meeting", 3))
        min_mentions = int(self.cfg.get("min_runner_mentions_for_general_video", 2))
        gen_keywords = self.cfg.get("general_preview_keywords") or [
            "best bets", "racing preview", "midweek", "saturday racing", "tips",
        ]

        matched_venue = match_videos_to_meeting(recent_videos, meeting_name, aliases=aliases, max_videos=max_vids)
        venue_ids = {v["video_id"] for v in matched_venue}

        urls: List[str] = []
        transcript_parts: List[str] = []
        seen_urls = set()

        # 1) Venue-matched Atom XML videos: always capture <title> + <media:description>, plus best-effort captions
        for v in matched_venue:
            u = v.get("url") or ""
            if not u or u in seen_urls:
                continue
            seen_urls.add(u)
            urls.append(u)

            meta_text = build_video_metadata_text(v)
            captions = self._get_cached_transcript(u)
            combined_entry = "\n".join(part for part in (meta_text, captions) if part).strip()
            if combined_entry:
                transcript_parts.append(combined_entry)
                logger.info(
                    "YouTube venue preview [%s] '%s' matched %s (xml_desc=%dc, captions=%dc)",
                    v.get("channel"),
                    v.get("title"),
                    meeting_name,
                    len(v.get("description") or ""),
                    len(captions),
                )

        # 2) Any manually configured URLs
        for u in list(extra_urls or []):
            if u and u not in seen_urls:
                seen_urls.add(u)
                urls.append(u)
                tx = self._get_cached_transcript(u)
                if tx:
                    transcript_parts.append(tx)

        # 3) Zero-extra-HTTP scan of <media:description> across ALL recent channel videos for >=2 runner mentions
        if runner_names and len(urls) < max_vids:
            for v in recent_videos:
                if len(urls) >= max_vids:
                    break
                vid = v.get("video_id")
                u = v.get("url") or ""
                if not u or vid in venue_ids or u in seen_urls:
                    continue
                meta_text = build_video_metadata_text(v)
                if not meta_text:
                    continue
                mentions = count_runner_mentions(meta_text, runner_names)
                if mentions >= min_mentions:
                    logger.info(
                        "YouTube XML <media:description> [%s] '%s' matched %d runners for %s!",
                        v.get("channel"),
                        v.get("title"),
                        mentions,
                        meeting_name,
                    )
                    seen_urls.add(u)
                    urls.append(u)
                    captions = self._get_cached_transcript(u)
                    combined_entry = "\n".join(part for part in (meta_text, captions) if part).strip()
                    transcript_parts.append(combined_entry)

        # 4) Best-effort caption scan on general daily preview titles if slots remain
        if runner_names and len(urls) < max_vids:
            general_candidates = match_general_preview_videos(
                recent_videos,
                keywords=gen_keywords,
                exclude_ids=venue_ids,
                max_candidates=5,
            )
            for v in general_candidates:
                if len(urls) >= max_vids:
                    break
                u = v["url"]
                if u in seen_urls:
                    continue
                tx = self._get_cached_transcript(u)
                if not tx:
                    continue
                mentions = count_runner_mentions(tx, runner_names)
                if mentions >= min_mentions:
                    logger.info(
                        "General YouTube preview captions [%s] '%s' matched %d runners for %s!",
                        v["channel"],
                        v["title"],
                        mentions,
                        meeting_name,
                    )
                    seen_urls.add(u)
                    urls.append(u)
                    meta_text = build_video_metadata_text(v)
                    combined_entry = "\n".join(part for part in (meta_text, tx) if part).strip()
                    transcript_parts.append(combined_entry)

        if not transcript_parts:
            return urls, ""

        combined_raw = "\n\n".join(transcript_parts)
        focused = extract_runner_relevant_snippets(combined_raw, runner_names or [], max_total_chars=5000)
        return urls, focused
