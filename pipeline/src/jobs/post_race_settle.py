from __future__ import annotations

import argparse
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from pipeline.src.engine.scorer_23pt import compute_meeting_bias
from pipeline.src.engine.settlement import settle_meeting_quaddie, settle_race
from pipeline.src.firestore_client import FirestoreStore
from pipeline.src.http_client import PoliteHttpClient
from pipeline.src.jobs.pre_jump_analyze import parse_utc_timestamp
from pipeline.src.sources.betfair_bsp import BetfairBspSource
from pipeline.src.sources.ladbrokes import LadbrokesSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("post_race_settle")

SYDNEY_TZ = ZoneInfo("Australia/Sydney")


def _race_has_jumped(advertised_start: str, now_utc: datetime) -> bool:
    if not advertised_start:
        return True
    dt = parse_utc_timestamp(advertised_start)
    if dt is None:
        return True
    return now_utc >= (dt + timedelta(minutes=3))


def _has_missing_exotic_dividend(settlement: Optional[Dict[str, Any]]) -> bool:
    """Return True if any winning exotic bet (Exacta/Trifecta/First4) has a missing or $0 dividend/return."""
    if not isinstance(settlement, dict):
        return False
    for key in ("exacta", "trifecta", "first4"):
        bet = settlement.get(key)
        if isinstance(bet, dict) and bet.get("won"):
            div = bet.get("dividend")
            ret = bet.get("return") or 0.0
            if not div or float(div) <= 0.0 or float(ret) <= 0.0:
                return True
    return False


def _has_unvoided_null_position(settlement: Optional[Dict[str, Any]]) -> bool:
    """Return True if an older settlement doc graded a null-position runner as a loss instead of void."""
    if not isinstance(settlement, dict):
        return False
    wa = settlement.get("win_all")
    if isinstance(wa, dict) and not wa.get("void") and (wa.get("finish_position") is None):
        return True
    return False


def _settled_within_hours(race: Dict[str, Any], now_utc: datetime, hours: float = 48.0) -> bool:
    """Return True if a settled race was settled (or jumped) within the last `hours` hours."""
    ts = race.get("settled_at") or (race.get("settlement") or {}).get("settled_at") or race.get("advertised_start") or ""
    dt = parse_utc_timestamp(ts)
    if dt is None:
        return True
    age_hours = (now_utc - dt).total_seconds() / 3600.0
    return age_hours <= hours


def run_settlement_for_date(date_str: str, store: FirestoreStore, http: PoliteHttpClient) -> None:
    races = store.query_by_field("races", "date", date_str)
    if not races:
        logger.info("No races found for settlement on %s.", date_str)
        return

    now_utc = datetime.now(timezone.utc)
    ladbrokes = LadbrokesSource(http)
    betfair = BetfairBspSource(http)

    # Fetch Betfair daily BSP CSV once per date (returns empty dict gracefully if not yet published)
    bsp_lookup = betfair.fetch_daily_bsp(date_str)

    by_meeting: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    settled_updates = 0
    cleaned_baseline = 0

    for race in races:
        slug = race.get("meeting_slug") or "unknown"
        status_lower = (race.get("status") or "").lower()
        already_settled = bool(race.get("settled")) or (status_lower == "settled")
        existing_settlement = race.get("settlement") or {}

        # Settle any race that completed pre-jump analysis OR morning Gemini baseline analysis
        # so GitHub cron jitter never skips a race or breaks a meeting's Quaddie.
        if not (race.get("pre_jump_analyzed") or race.get("baseline_analyzed")):
            if already_settled:
                store.delete_doc("settlements", race["race_id"])
                store.set_doc(
                    "races",
                    race["race_id"],
                    {"settled": False, "settlement": None, "updated_at": now_utc.isoformat()},
                    merge=True,
                )
                cleaned_baseline += 1
            by_meeting[slug].append(race)
            continue

        within_48h = _settled_within_hours(race, now_utc, hours=48.0)
        bsp_reconciled = bool(existing_settlement.get("bsp_reconciled"))
        missing_exotic_div = _has_missing_exotic_dividend(existing_settlement)
        needs_void_repair = _has_unvoided_null_position(existing_settlement)

        # Re-process if:
        # 1) Not settled yet and past jump time, OR
        # 2) Settled within the past 48h and missing an exotic dividend / needs void repair, OR
        # 3) Settled within the past 48h and Betfair BSP CSV is now available.
        needs_fetch = (
            (not already_settled and _race_has_jumped(race.get("advertised_start") or "", now_utc))
            or (within_48h and (missing_exotic_div or needs_void_repair))
        )
        needs_bsp_enrich = (already_settled and within_48h and not bsp_reconciled and bool(bsp_lookup))

        if not needs_fetch and not needs_bsp_enrich:
            by_meeting[slug].append(race)
            continue

        results = race.get("results") or []
        dividends = race.get("dividends") or {}
        status = race.get("status") or "Open"
        scratched_numbers = {
            int(r.get("runner_number") or 0)
            for r in (race.get("scored_runners") or [])
            if r.get("is_scratched")
        }

        if needs_fetch:
            try:
                ev = ladbrokes.fetch_race_event(race["race_id"])
                results = ev.get("results") or []
                dividends = ev.get("dividends") or {}
                status = ev.get("status") or status
                for lr in ev.get("runners") or []:
                    if lr.get("is_scratched"):
                        scratched_numbers.add(int(lr.get("runner_number") or 0))
            except Exception as exc:
                logger.error("Failed fetching post-race results for %s: %s", race["race_id"], exc)
                by_meeting[slug].append(race)
                continue

        if not results or status.lower() not in ("final", "paying", "closed", "settled"):
            by_meeting[slug].append(race)
            continue

        settlement_doc = settle_race(
            race,
            results=results,
            dividends=dividends,
            bsp_lookup=bsp_lookup,
            scratched_numbers=scratched_numbers,
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        settlement_doc["settled_at"] = race.get("settled_at") or now_iso

        race_update = {
            "status": "Settled",
            "results": results,
            "dividends": dividends,
            "settlement": settlement_doc,
            "settled": True,
            "settled_at": settlement_doc["settled_at"],
            "updated_at": now_iso,
        }
        store.set_doc("races", race["race_id"], race_update, merge=True)
        store.set_doc("settlements", settlement_doc["settlement_id"], settlement_doc, merge=True)
        settled_updates += 1

        by_meeting[slug].append({**race, **race_update})

    # Update live Current Meeting Track Bias and settle Meeting Quaddies
    for slug, m_races in by_meeting.items():
        meeting_doc_id = f"{date_str}_{slug}"
        meeting = store.get_doc("meetings", meeting_doc_id)
        if not meeting:
            continue

        settled_m_races = [r for r in m_races if r.get("results")]
        meeting_bias = compute_meeting_bias(settled_m_races)
        # Include any leg that completed pre-jump OR morning Gemini baseline analysis
        analyzed_m_races = [r for r in m_races if r.get("pre_jump_analyzed") or r.get("baseline_analyzed")]
        quaddie_settlement = settle_meeting_quaddie(meeting, analyzed_m_races)

        update_m: Dict[str, Any] = {
            "meeting_bias": meeting_bias,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if quaddie_settlement:
            quaddie_settlement["settled_at"] = update_m["updated_at"]
            update_m["quaddie_settlement"] = quaddie_settlement
            store.set_doc("settlements", quaddie_settlement["settlement_id"], quaddie_settlement, merge=True)

        store.set_doc("meetings", meeting_doc_id, update_m, merge=True)

    logger.info(
        "Post-race settlement finished for %s (%d race(s) settled/enriched, %d non-analyzed baseline settlement(s) cleaned).",
        date_str,
        settled_updates,
        cleaned_baseline,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Post-race settlement & Betfair BSP reconciliation")
    parser.add_argument("--date", default=os.environ.get("TARGET_DATE", "").strip(), help="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Use local JSON store")
    args = parser.parse_args()

    store = FirestoreStore(dry_run=args.dry_run)
    http = PoliteHttpClient(min_delay_seconds=3.0)

    if args.date:
        dates = [args.date]
    else:
        today = datetime.now(SYDNEY_TZ).date()
        # Multi-day lookback (today + past 3 days) so night meetings, delayed exotic dividends,
        # and next-morning Betfair BSP CSV reconciliation are always caught.
        dates = [(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(4)]

    for d in dates:
        run_settlement_for_date(d, store=store, http=http)


if __name__ == "__main__":
    main()
