from __future__ import annotations

import argparse
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from pipeline.src.engine.gemini_batch import GeminiBatchAnalyzer
from pipeline.src.engine.pricing import build_meeting_quaddie, price_and_generate_bets
from pipeline.src.engine.scorer_23pt import compute_meeting_bias, score_race_23pt
from pipeline.src.firestore_client import FirestoreStore
from pipeline.src.http_client import PoliteHttpClient
from pipeline.src.sources.ladbrokes import LadbrokesSource
from pipeline.src.sources.youtube import fetch_transcripts_for_urls, load_configured_youtube_urls

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("pre_jump_analyze")

SYDNEY_TZ = ZoneInfo("Australia/Sydney")

# Widened pre-jump scan window (T-2m to T-120m) so GitHub Actions cron jitter never skips a race.
# Races analyzed early (T-40..T-120m) are tagged 'early' and upgraded/locked once more at T-2..T-40m ('final').
WINDOW_MIN_MINUTES = 2.0
WINDOW_MAX_MINUTES = 120.0
FINAL_LOCK_THRESHOLD_MINUTES = 40.0


def parse_utc_timestamp(ts: str) -> Optional[datetime]:
    """Parse ISO-8601 ('...Z') and Go-formatted ('2026-11-25 03:55:00 +0000 UTC') timestamps."""
    raw = (ts or "").strip()
    if not raw:
        return None
    # Strip trailing ' UTC' emitted by Ladbrokes Go time.Time.String()
    if raw.endswith(" UTC"):
        raw = raw[:-4].strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def _minutes_until_jump(advertised_start: str, now_utc: datetime) -> Optional[float]:
    dt = parse_utc_timestamp(advertised_start)
    if dt is None:
        return None
    return (dt - now_utc).total_seconds() / 60.0


def run_pre_jump_analysis(date_str: str, dry_run: bool = False, force_all: bool = False) -> None:
    store = FirestoreStore(dry_run=dry_run)
    races = store.query_by_field("races", "date", date_str)
    if not races:
        logger.info("No races in Firestore for %s. Exiting quickly.", date_str)
        return

    now_utc = datetime.now(timezone.utc)
    by_meeting: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    all_by_meeting: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for race in races:
        slug = race.get("meeting_slug") or "unknown"
        all_by_meeting[slug].append(race)

        status = (race.get("status") or "Open").lower()
        if status in ("final", "closed", "abandoned", "paying", "settled"):
            continue

        mins = _minutes_until_jump(race.get("advertised_start") or "", now_utc)
        already_analyzed = bool(race.get("pre_jump_analyzed"))
        stage = race.get("pre_jump_stage") or ("final" if already_analyzed else None)

        # Skip if already locked in the final T-2..T-40m window (unless force_all)
        if already_analyzed and stage == "final" and not force_all:
            continue
        # If an early T-40..T-120m pass already ran, only re-score once the race enters T-2..T-40m
        if already_analyzed and stage == "early" and not force_all:
            if mins is None or not (WINDOW_MIN_MINUTES <= mins <= FINAL_LOCK_THRESHOLD_MINUTES):
                continue

        if force_all or (mins is not None and WINDOW_MIN_MINUTES <= mins <= WINDOW_MAX_MINUTES):
            race["_mins_to_jump"] = mins
            by_meeting[slug].append(race)

    if not by_meeting:
        logger.info(
            "No races requiring pre-jump analysis within T-%.0fm..T-%.0fm window for %s. Exiting cleanly.",
            WINDOW_MIN_MINUTES,
            WINDOW_MAX_MINUTES,
            date_str,
        )
        return

    http = PoliteHttpClient(min_delay_seconds=3.0)
    ladbrokes = LadbrokesSource(http)
    gemini = GeminiBatchAnalyzer()
    yt_fs_config = store.get_doc("config", "youtube_previews")

    for slug, pending_races in by_meeting.items():
        meeting_doc_id = f"{date_str}_{slug}"
        meeting = store.get_doc("meetings", meeting_doc_id) or {}
        track_meta = meeting.get("track_meta") or {}
        weather = meeting.get("weather") or {}
        meeting_name = meeting.get("display_name") or pending_races[0].get("meeting_name") or slug

        # Compute live Current Meeting Track Bias from any earlier finalized races at this venue today
        earlier_settled = [
            r for r in all_by_meeting[slug] if (r.get("results") or []) and (r.get("status") or "").lower() in ("final", "paying")
        ]
        meeting_bias = compute_meeting_bias(earlier_settled)

        # Pull configured + auto-discovered YouTube preview transcripts once per meeting
        stored_yt = []
        for r in pending_races:
            stored_yt.extend(r.get("youtube_urls_used") or [])
        yt_urls = list(dict.fromkeys(stored_yt + load_configured_youtube_urls(date_str, slug, firestore_config=yt_fs_config)))
        yt_transcript = fetch_transcripts_for_urls(yt_urls) if yt_urls else ""

        logger.info(
            "Pre-jump batch for %s: %d race(s) in T-5..T-60m window (YT URLs: %d, earlier bias sample: %d)",
            meeting_name,
            len(pending_races),
            len(yt_urls),
            meeting_bias["sample_size"],
        )

        refreshed_races: List[Dict[str, Any]] = []
        for race in sorted(pending_races, key=lambda x: int(x.get("race_number") or 0)):
            race_id = race["race_id"]
            # Preserve morning opening odds snapshot
            open_map = {
                int(r["runner_number"]): float(r.get("opening_win") or 0.0)
                for r in (race.get("scored_runners") or [])
                if float(r.get("opening_win") or 0.0) > 1.0
            }

            try:
                live_ev = ladbrokes.fetch_race_event(race_id)
            except Exception as exc:
                logger.error("Failed live refresh for race %s: %s", race_id, exc)
                continue

            for runner in live_ev["runners"]:
                rn = int(runner["runner_number"])
                if rn in open_map:
                    runner["opening_win"] = open_map[rn]
                elif runner["opening_win"] <= 1.0 and runner["live_win"] > 1.0:
                    runner["opening_win"] = runner["live_win"]

            live_ev["date"] = date_str
            live_ev["meeting_slug"] = slug
            live_ev["meeting_name"] = meeting_name
            live_ev["state"] = race.get("state") or meeting.get("state", "AUS")
            live_ev["tier"] = race.get("tier") or meeting.get("tier", "Provincial")

            scored = score_race_23pt(
                live_ev,
                track_meta=track_meta,
                weather=weather,
                meeting_bias=meeting_bias,
                youtube_transcript=yt_transcript,
            )
            live_ev["scored_runners"] = scored
            refreshed_races.append(live_ev)

        if not refreshed_races:
            continue

        # Batch-analyze entire meeting chunk with Gemini 3.1 Flash Lite (10s spacing + strict anti-hallucination guard)
        ai_meta = gemini.analyze_meeting_batch(
            meeting_name=meeting_name,
            weather=weather,
            meeting_bias=meeting_bias,
            races=refreshed_races,
            youtube_transcript=yt_transcript,
        )
        logger.info("Gemini batch status for %s: %s", meeting_name, ai_meta)

        now_iso = datetime.now(timezone.utc).isoformat()
        mins_lookup = {r["race_id"]: r.get("_mins_to_jump") for r in pending_races}
        for live_ev in refreshed_races:
            pricing = price_and_generate_bets(live_ev)
            m_to_jump = mins_lookup.get(live_ev["race_id"])
            stage = "final" if (force_all or m_to_jump is None or m_to_jump <= FINAL_LOCK_THRESHOLD_MINUTES) else "early"
            update_doc = {
                "status": live_ev["status"],
                "track_condition": live_ev["track_condition"],
                "track_condition_band": live_ev["track_condition_band"],
                "rail_position": live_ev["rail_position"],
                "meeting_bias": meeting_bias,
                "scored_runners": live_ev["scored_runners"],
                "win_selection": pricing["win_selection"],
                "top_10": pricing["top_10"],
                "srm_combos": pricing["srm_combos"],
                "exotics": pricing["exotics"],
                "ai_summary": live_ev.get("ai_summary", ""),
                "ai_model": live_ev.get("ai_model"),
                "youtube_urls_used": yt_urls,
                "pre_jump_analyzed": True,
                "pre_jump_stage": stage,
                "pre_jump_analyzed_at": now_iso,
                "updated_at": now_iso,
            }
            store.set_doc("races", live_ev["race_id"], update_doc, merge=True)

            # Update in-memory list for Quaddie refresh
            for idx, existing_r in enumerate(all_by_meeting[slug]):
                if existing_r["race_id"] == live_ev["race_id"]:
                    all_by_meeting[slug][idx] = {**existing_r, **update_doc}
                    break

        # Refresh meeting Quaddie & live track bias
        if meeting:
            quaddie = build_meeting_quaddie(meeting, all_by_meeting[slug])
            store.set_doc(
                "meetings",
                meeting_doc_id,
                {
                    "meeting_bias": meeting_bias,
                    "quaddie_ticket": quaddie,
                    "updated_at": now_iso,
                },
                merge=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-jump T-5..T-60m analysis & Gemini batch engine")
    parser.add_argument("--date", default=os.environ.get("TARGET_DATE", "").strip(), help="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Use local JSON store")
    parser.add_argument("--force-all", action="store_true", help="Analyze all open races regardless of jump window")
    args = parser.parse_args()

    force_all = args.force_all or os.environ.get("FORCE_ALL", "").lower() == "true"
    date_str = args.date or datetime.now(SYDNEY_TZ).strftime("%Y-%m-%d")
    run_pre_jump_analysis(date_str=date_str, dry_run=args.dry_run, force_all=force_all)


if __name__ == "__main__":
    main()
