from __future__ import annotations

import argparse
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
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

    for race in races:
        slug = race.get("meeting_slug") or "unknown"
        already_settled = bool(race.get("settled"))
        bsp_reconciled = bool((race.get("settlement") or {}).get("bsp_reconciled"))

        # Re-process if not settled yet OR if Betfair BSP just became available for yesterday's races
        needs_fetch = (not already_settled and _race_has_jumped(race.get("advertised_start") or "", now_utc))
        needs_bsp_enrich = (already_settled and not bsp_reconciled and bool(bsp_lookup))

        if not needs_fetch and not needs_bsp_enrich:
            by_meeting[slug].append(race)
            continue

        results = race.get("results") or []
        dividends = race.get("dividends") or {}
        status = race.get("status") or "Open"

        if needs_fetch:
            try:
                ev = ladbrokes.fetch_race_event(race["race_id"])
                results = ev.get("results") or []
                dividends = ev.get("dividends") or {}
                status = ev.get("status") or status
            except Exception as exc:
                logger.error("Failed fetching post-race results for %s: %s", race["race_id"], exc)
                by_meeting[slug].append(race)
                continue

        if not results or status.lower() not in ("final", "paying", "closed"):
            by_meeting[slug].append(race)
            continue

        settlement_doc = settle_race(race, results=results, dividends=dividends, bsp_lookup=bsp_lookup)
        now_iso = datetime.now(timezone.utc).isoformat()
        settlement_doc["settled_at"] = now_iso

        race_update = {
            "status": "Final",
            "results": results,
            "dividends": dividends,
            "settlement": settlement_doc,
            "settled": True,
            "settled_at": now_iso,
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
        quaddie_settlement = settle_meeting_quaddie(meeting, m_races)

        update_m: Dict[str, Any] = {
            "meeting_bias": meeting_bias,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if quaddie_settlement:
            quaddie_settlement["settled_at"] = update_m["updated_at"]
            update_m["quaddie_settlement"] = quaddie_settlement
            store.set_doc("settlements", quaddie_settlement["settlement_id"], quaddie_settlement, merge=True)

        store.set_doc("meetings", meeting_doc_id, update_m, merge=True)

    logger.info("Post-race settlement finished for %s (%d race(s) settled/enriched).", date_str, settled_updates)


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
        # Process today for real-time settlement + yesterday for next-morning Betfair BSP reconciliation
        dates = [today.strftime("%Y-%m-%d"), (today - timedelta(days=1)).strftime("%Y-%m-%d")]

    for d in dates:
        run_settlement_for_date(d, store=store, http=http)


if __name__ == "__main__":
    main()
