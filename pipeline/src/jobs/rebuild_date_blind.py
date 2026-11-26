from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from pipeline.src.engine.gemini_batch import GeminiBatchAnalyzer
from pipeline.src.engine.pricing import build_meeting_quaddie, price_and_generate_bets
from pipeline.src.engine.scorer_23pt import compute_meeting_bias, score_race_23pt
from pipeline.src.firestore_client import FirestoreStore
from pipeline.src.http_client import PoliteHttpClient
from pipeline.src.jobs.post_race_settle import run_settlement_for_date
from pipeline.src.sources.ladbrokes import LadbrokesSource
from pipeline.src.sources.weather import WeatherSource
from pipeline.src.sources.youtube import load_configured_youtube_urls
from pipeline.src.sources.youtube_feed import YouTubeFeedDiscovery

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("rebuild_date_blind")

FORBIDDEN_RUNNER_KEYS = {
    "finishing_position",
    "position",
    "margin",
    "margin_length",
    "official_top4",
    "result",
    "results",
    "dividends",
    "post_race_comment",
}


def scrub_event_blind(ev: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strictly strip ALL post-race results, dividends, margins, finishing positions, and
    post-race wrap commentary from a race event BEFORE scoring, Gemini, or exotic pricing.
    """
    clean = dict(ev)
    clean["status"] = "Open"
    clean["results"] = []
    clean["dividends"] = {}
    clean["race_comment"] = ""
    clean.pop("official_top4", None)
    clean.pop("settlement", None)

    clean_runners: List[Dict[str, Any]] = []
    for r in ev.get("runners") or []:
        cr = {k: v for k, v in r.items() if k not in FORBIDDEN_RUNNER_KEYS}
        cr["form_comment"] = ""
        clean_runners.append(cr)
    clean["runners"] = clean_runners
    return clean


def wipe_date_documents(store: FirestoreStore, date_str: str) -> Dict[str, int]:
    """Delete all existing meetings, races, and settlements for date_str."""
    deleted = {}
    for col in ("meetings", "races", "settlements"):
        docs = store.query_by_field(col, "date", date_str)
        for doc in docs:
            doc_id = doc.get("_doc_id") or doc.get("race_id") or doc.get("settlement_id")
            if doc_id:
                store.delete_doc(col, str(doc_id))
        deleted[col] = len(docs)
    logger.info("Wiped existing %s documents from Firestore: %s", date_str, deleted)
    return deleted


def run_blind_two_phase_rebuild(
    date_str: str,
    yt_cutoff_iso: str,
    yt_lookback_hours: float = 60.0,
    dry_run: bool = False,
) -> None:
    store = FirestoreStore(dry_run=dry_run)
    http = PoliteHttpClient(min_delay_seconds=3.0)
    ladbrokes = LadbrokesSource(http)
    weather_src = WeatherSource(http)
    gemini = GeminiBatchAnalyzer()
    yt_discovery = YouTubeFeedDiscovery(http)
    yt_fs_config = store.get_doc("config", "youtube_previews")

    yt_cutoff_utc = datetime.fromisoformat(yt_cutoff_iso.replace("Z", "+00:00")).astimezone(timezone.utc)

    # STEP 0: Clean wipe of existing date documents
    wipe_date_documents(store, date_str)

    # PHASE A: Strictly blind pre-race ingestion, Gemini analysis & exotic payout gating
    logger.info("=== PHASE A: Blind Pre-Race Rebuild for %s (YT cutoff < %s, lookback %.0fh) ===", date_str, yt_cutoff_utc.isoformat(), yt_lookback_hours)
    meetings = ladbrokes.fetch_aus_meetings(date_str, include_country=False)
    if not meetings:
        logger.warning("No AUS Metro/Provincial meetings found for %s.", date_str)
        return

    # Only allow YouTube videos published STRICTLY BEFORE yt_cutoff_utc (prior to Race 1)
    recent_yt_videos = yt_discovery.fetch_recent_channel_videos(
        lookback_hours=yt_lookback_hours,
        upper_bound_utc=yt_cutoff_utc,
    )
    logger.info("Blind pre-cutoff YouTube videos discovered: %d", len(recent_yt_videos))

    now_iso = datetime.now(timezone.utc).isoformat()
    total_races_locked = 0
    exotic_skip_counts = {"exacta": 0, "trifecta": 0, "first4": 0}
    exotic_active_counts = {"exacta": 0, "trifecta": 0, "first4": 0}

    for meeting in meetings:
        slug = meeting["meeting_slug"]
        meeting_doc_id = f"{date_str}_{slug}"
        logger.info("Phase A — %s (%s, %s): %d races", meeting["display_name"], meeting["state"], meeting["tier"], len(meeting["races"]))

        weather = weather_src.fetch_track_weather(meeting["track_meta"])
        # Force neutral 5.0 meeting bias so later races cannot leak bias into earlier races
        neutral_bias = compute_meeting_bias([])
        manual_yt_urls = load_configured_youtube_urls(date_str, slug, firestore_config=yt_fs_config)

        blind_events: List[Dict[str, Any]] = []
        summary_map: Dict[str, Dict[str, Any]] = {}
        rail_position = "True"

        for r_summary in meeting["races"]:
            race_id = r_summary["race_id"]
            if not race_id:
                continue
            raw_ev = ladbrokes.fetch_race_event(race_id)
            ev = scrub_event_blind(raw_ev)

            rail_position = ev.get("rail_position") or rail_position
            ev["date"] = date_str
            ev["meeting_slug"] = slug
            ev["meeting_name"] = meeting["display_name"]
            ev["state"] = meeting["state"]
            ev["tier"] = meeting["tier"]
            ev["weather"] = weather
            ev["track_meta"] = meeting["track_meta"]
            summary_map[race_id] = r_summary
            blind_events.append(ev)

        all_runner_names = [
            r.get("name") or ""
            for ev in blind_events
            for r in (ev.get("runners") or [])
            if not r.get("is_scratched")
        ]
        yt_urls, yt_transcript = yt_discovery.fetch_meeting_transcript(
            recent_videos=recent_yt_videos,
            meeting_name=meeting["display_name"],
            aliases=(meeting.get("track_meta") or {}).get("aliases") or [meeting["name"]],
            extra_urls=manual_yt_urls,
            runner_names=all_runner_names,
        )

        for ev in blind_events:
            ev["scored_runners"] = score_race_23pt(
                ev,
                meeting["track_meta"],
                weather,
                meeting_bias=neutral_bias,
                youtube_transcript=yt_transcript,
            )

        ai_meta = gemini.analyze_meeting_batch(
            meeting_name=meeting["display_name"],
            weather=weather,
            meeting_bias=neutral_bias,
            races=blind_events,
            youtube_transcript=yt_transcript,
        )
        logger.info("Phase A Gemini batch for %s: %s", meeting["display_name"], ai_meta)

        meeting_races: List[Dict[str, Any]] = []
        for ev in blind_events:
            race_id = ev["race_id"]
            r_summary = summary_map.get(race_id, {})
            pricing = price_and_generate_bets(ev)

            for ek in ("exacta", "trifecta", "first4"):
                ex = (pricing.get("exotics") or {}).get(ek)
                if ex:
                    if ex.get("skipped"):
                        exotic_skip_counts[ek] += 1
                    else:
                        exotic_active_counts[ek] += 1

            race_doc = {
                "race_id": race_id,
                "date": date_str,
                "meeting_id": meeting["meeting_id"],
                "meeting_slug": slug,
                "meeting_name": meeting["display_name"],
                "state": meeting["state"],
                "tier": meeting["tier"],
                "race_number": ev["race_number"],
                "name": ev["name"],
                "status": "Open",
                "advertised_start": ev["advertised_start"] or r_summary.get("start_time") or "",
                "distance": ev["distance"],
                "distance_band": ev["distance_band"],
                "track_condition": ev["track_condition"],
                "track_condition_band": ev["track_condition_band"],
                "rail_position": ev["rail_position"],
                "track_direction": ev["track_direction"] or meeting["track_meta"].get("turning"),
                "track_home_straight": ev["track_home_straight"] or meeting["track_meta"].get("home_straight_m"),
                "weather": weather,
                "meeting_bias": neutral_bias,
                "race_comment": "",
                "prize_money": ev["prize_money"],
                "positions_paid": ev["positions_paid"],
                "scored_runners": ev["scored_runners"],
                "win_selection": pricing["win_selection"],
                "top_10": pricing["top_10"],
                "srm_combos": pricing["srm_combos"],
                "exotics": pricing["exotics"],
                "results": [],
                "dividends": {},
                "baseline_analyzed": True,
                "baseline_analyzed_at": now_iso,
                "pre_jump_analyzed": True,
                "pre_jump_stage": "final",
                "pre_jump_analyzed_at": now_iso,
                "settled": False,
                "ai_summary": ev.get("ai_summary") or "",
                "ai_model": ev.get("ai_model"),
                "youtube_urls_used": yt_urls,
                "blind_rebuild": True,
                "ingested_at": now_iso,
                "updated_at": now_iso,
            }
            store.set_doc("races", race_id, race_doc, merge=False)
            meeting_races.append(race_doc)
            total_races_locked += 1

        quaddie_ticket = build_meeting_quaddie(meeting, meeting_races)
        meeting_doc = {
            "meeting_id": meeting["meeting_id"],
            "meeting_slug": slug,
            "date": date_str,
            "name": meeting["name"],
            "display_name": meeting["display_name"],
            "state": meeting["state"],
            "tier": meeting["tier"],
            "track_condition": meeting["track_condition"],
            "track_condition_band": meeting["track_condition_band"],
            "rail_position": rail_position,
            "quaddie_races": meeting["quaddie_races"],
            "quaddie_ticket": quaddie_ticket,
            "weather": weather,
            "track_meta": meeting["track_meta"],
            "meeting_bias": neutral_bias,
            "race_count": len(meeting_races),
            "race_ids": [r["race_id"] for r in meeting_races],
            "blind_rebuild": True,
            "updated_at": now_iso,
        }
        store.set_doc("meetings", meeting_doc_id, meeting_doc, merge=False)

    logger.info(
        "Phase A COMPLETE & LOCKED for %s: %d meetings, %d races | Active exotics=%s | Skipped low-payout exotics=%s",
        date_str,
        len(meetings),
        total_races_locked,
        exotic_active_counts,
        exotic_skip_counts,
    )

    # PHASE B: Attach official Ladbrokes finishing orders/dividends & settle all 30 races + 4 Quaddies
    logger.info("=== PHASE B: Official Post-Race Result Attachment & Settlement for %s ===", date_str)
    run_settlement_for_date(date_str, store=store, http=http)
    logger.info("=== Blind Two-Phase Rebuild Complete for %s ===", date_str)


def main() -> None:
    parser = argparse.ArgumentParser(description="Strict two-phase bias-free historical date rebuild")
    parser.add_argument("--date", required=False, default=os.environ.get("TARGET_DATE", "2026-11-25").strip())
    parser.add_argument("--yt-cutoff-utc", default=os.environ.get("YT_CUTOFF_UTC", "2026-11-25T01:00:00Z").strip())
    parser.add_argument("--yt-lookback-hours", type=float, default=float(os.environ.get("YT_LOOKBACK_HOURS", "60")))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run_blind_two_phase_rebuild(
        date_str=args.date,
        yt_cutoff_iso=args.yt_cutoff_utc,
        yt_lookback_hours=args.yt_lookback_hours,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
