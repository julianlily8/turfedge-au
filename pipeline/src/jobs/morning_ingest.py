from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pipeline.src.engine.gemini_batch import GeminiBatchAnalyzer
from pipeline.src.engine.pricing import build_meeting_quaddie, price_and_generate_bets
from pipeline.src.engine.scorer_23pt import compute_meeting_bias, score_race_23pt
from pipeline.src.firestore_client import FirestoreStore
from pipeline.src.http_client import PoliteHttpClient
from pipeline.src.sources.ladbrokes import LadbrokesSource
from pipeline.src.sources.weather import WeatherSource
from pipeline.src.sources.youtube import fetch_transcripts_for_urls, load_configured_youtube_urls

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("morning_ingest")

SYDNEY_TZ = ZoneInfo("Australia/Sydney")


def should_run_morning_window(force: bool = False) -> bool:
    """Allow both primary (8:27 AM AEDT) and backup (9:17 AM AEDT / 8:17 AM AEST) morning cron runs."""
    if force:
        return True
    now_syd = datetime.now(SYDNEY_TZ)
    return now_syd.hour in (8, 9)


def _already_ingested_and_analyzed(store: FirestoreStore, date_str: str, require_gemini: bool) -> bool:
    """Return True if today's meetings and races are already in Firestore with baseline Gemini analysis."""
    existing_meetings = store.query_by_field("meetings", "date", date_str)
    if not existing_meetings:
        return False
    existing_races = store.query_by_field("races", "date", date_str)
    if not existing_races:
        return False
    all_baseline = all(bool(r.get("baseline_analyzed")) for r in existing_races)
    if not all_baseline:
        return False
    if require_gemini:
        any_gemini = any(bool(r.get("ai_model")) for r in existing_races)
        if not any_gemini:
            return False
    logger.info(
        "Idempotency check: %s already has %d meeting(s) & %d Gemini-analyzed race(s) in Firestore. Skipping duplicate run.",
        date_str,
        len(existing_meetings),
        len(existing_races),
    )
    return True


def run_morning_ingest(date_str: str, dry_run: bool = False, max_meetings: int = 0, force: bool = False) -> None:
    store = FirestoreStore(dry_run=dry_run)
    gemini = GeminiBatchAnalyzer()

    if not force and not dry_run and _already_ingested_and_analyzed(store, date_str, require_gemini=gemini.is_configured):
        return

    http = PoliteHttpClient(min_delay_seconds=3.0)
    ladbrokes = LadbrokesSource(http)
    weather_src = WeatherSource(http)
    yt_fs_config = store.get_doc("config", "youtube_previews")

    meetings = ladbrokes.fetch_aus_meetings(date_str, include_country=False)
    if max_meetings > 0:
        meetings = meetings[:max_meetings]

    if not meetings:
        logger.info("No Metro/Provincial AUS thoroughbred meetings found for %s.", date_str)
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    for meeting in meetings:
        slug = meeting["meeting_slug"]
        meeting_doc_id = f"{date_str}_{slug}"
        logger.info("Ingesting %s (%s, %s) — %d races", meeting["display_name"], meeting["state"], meeting["tier"], len(meeting["races"]))

        weather = weather_src.fetch_track_weather(meeting["track_meta"])
        meeting_bias = compute_meeting_bias([])
        yt_urls = load_configured_youtube_urls(date_str, slug, firestore_config=yt_fs_config)
        yt_transcript = fetch_transcripts_for_urls(yt_urls) if yt_urls else ""

        fetched_events = []
        existing_map = {}
        summary_map = {}
        rail_position = "True"

        for r_summary in meeting["races"]:
            race_id = r_summary["race_id"]
            if not race_id:
                continue
            try:
                ev = ladbrokes.fetch_race_event(race_id)
            except Exception as exc:
                logger.error("Failed fetching race %s: %s", race_id, exc)
                continue

            rail_position = ev.get("rail_position") or rail_position
            ev["date"] = date_str
            ev["meeting_slug"] = slug
            ev["meeting_name"] = meeting["display_name"]
            ev["state"] = meeting["state"]
            ev["tier"] = meeting["tier"]
            ev["weather"] = weather
            ev["track_meta"] = meeting["track_meta"]

            # Preserve morning opening prices if race already ingested earlier today
            existing = store.get_doc("races", race_id)
            existing_map[race_id] = existing
            summary_map[race_id] = r_summary
            existing_open_map = {}
            if existing:
                for er in existing.get("scored_runners") or existing.get("runners") or []:
                    if er.get("opening_win"):
                        existing_open_map[int(er["runner_number"])] = float(er["opening_win"])

            for runner in ev["runners"]:
                rn = int(runner["runner_number"])
                if rn in existing_open_map and existing_open_map[rn] > 1.0:
                    runner["opening_win"] = existing_open_map[rn]
                elif runner["opening_win"] <= 1.0 and runner["live_win"] > 1.0:
                    runner["opening_win"] = runner["live_win"]

            scored = score_race_23pt(
                ev,
                meeting["track_meta"],
                weather,
                meeting_bias=meeting_bias,
                youtube_transcript=yt_transcript,
            )
            ev["scored_runners"] = scored
            fetched_events.append(ev)

        # Run Gemini meeting-level batch at 8:27 AM so EVERY race on the card gets full
        # Gemini ratings, #1 Win, SRM, Exotic, and Quaddie picks immediately (immune to cron jitter!).
        if fetched_events:
            ai_meta = gemini.analyze_meeting_batch(
                meeting_name=meeting["display_name"],
                weather=weather,
                meeting_bias=meeting_bias,
                races=fetched_events,
                youtube_transcript=yt_transcript,
            )
            logger.info("Morning Gemini baseline batch for %s: %s", meeting["display_name"], ai_meta)

        meeting_races = []
        for ev in fetched_events:
            race_id = ev["race_id"]
            existing = existing_map.get(race_id)
            r_summary = summary_map.get(race_id, {})
            pricing = price_and_generate_bets(ev)

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
                "status": ev["status"],
                "advertised_start": ev["advertised_start"] or r_summary.get("start_time") or "",
                "distance": ev["distance"],
                "distance_band": ev["distance_band"],
                "track_condition": ev["track_condition"],
                "track_condition_band": ev["track_condition_band"],
                "rail_position": ev["rail_position"],
                "track_direction": ev["track_direction"] or meeting["track_meta"].get("turning"),
                "track_home_straight": ev["track_home_straight"] or meeting["track_meta"].get("home_straight_m"),
                "weather": weather,
                "meeting_bias": meeting_bias,
                "race_comment": ev["race_comment"],
                "prize_money": ev["prize_money"],
                "positions_paid": ev["positions_paid"],
                "scored_runners": ev["scored_runners"],
                "win_selection": pricing["win_selection"],
                "top_10": pricing["top_10"],
                "srm_combos": pricing["srm_combos"],
                "exotics": pricing["exotics"],
                "results": ev["results"],
                "dividends": ev["dividends"],
                "baseline_analyzed": True,
                "baseline_analyzed_at": now_iso,
                "pre_jump_analyzed": bool(existing.get("pre_jump_analyzed")) if existing else False,
                "pre_jump_stage": existing.get("pre_jump_stage") if existing else None,
                "settled": bool(existing.get("settled")) if existing else False,
                "ai_summary": ev.get("ai_summary") or (existing.get("ai_summary", "") if existing else ""),
                "ai_model": ev.get("ai_model") or (existing.get("ai_model") if existing else None),
                "youtube_urls_used": yt_urls,
                "ingested_at": now_iso,
                "updated_at": now_iso,
            }
            store.set_doc("races", race_id, race_doc, merge=True)
            meeting_races.append(race_doc)

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
            "meeting_bias": meeting_bias,
            "race_count": len(meeting_races),
            "race_ids": [r["race_id"] for r in meeting_races],
            "updated_at": now_iso,
        }
        store.set_doc("meetings", meeting_doc_id, meeting_doc, merge=True)

    logger.info("Morning ingestion completed for %s (%d meetings).", date_str, len(meetings))


def main() -> None:
    parser = argparse.ArgumentParser(description="Morning Australian horse racing ingestion")
    parser.add_argument("--date", default=os.environ.get("TARGET_DATE", "").strip(), help="YYYY-MM-DD (defaults to today in Australia/Sydney)")
    parser.add_argument("--dry-run", action="store_true", help="Write to local JSON store instead of Firestore")
    parser.add_argument("--force", action="store_true", help="Bypass 8 AM Sydney local-hour cron guard")
    parser.add_argument("--max-meetings", type=int, default=0, help="Limit meetings for fast local testing")
    args = parser.parse_args()

    force = args.force or os.environ.get("FORCE_RUN", "").lower() == "true" or args.dry_run
    if not should_run_morning_window(force=force):
        logger.info("Skipping run: current Australia/Sydney hour is %d (outside 08:xx–09:xx morning window).", datetime.now(SYDNEY_TZ).hour)
        return

    date_str = args.date or datetime.now(SYDNEY_TZ).strftime("%Y-%m-%d")
    run_morning_ingest(date_str=date_str, dry_run=args.dry_run, max_meetings=args.max_meetings, force=force)


if __name__ == "__main__":
    main()
