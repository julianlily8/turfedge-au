from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pipeline.src.http_client import PoliteHttpClient

logger = logging.getLogger(__name__)

BETFAIR_BSP_BASE = "https://promo.betfair.com/betfairsp/prices"


def normalize_horse_name(name: str) -> str:
    """Normalize runner names across Ladbrokes and Betfair (strip country tags, runner numbers, punctuation)."""
    s = (name or "").lower().strip()
    s = re.sub(r"^\d+\.\s*", "", s)  # strip leading "5. "
    s = re.sub(r"\([a-z]{2,3}\)", "", s)  # strip "(nz)", "(gb)", etc.
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


class BetfairBspSource:
    """Downloads official zero-key Betfair daily Australian Win & Place BSP CSV promo files."""

    def __init__(self, http: PoliteHttpClient) -> None:
        self.http = http

    @staticmethod
    def _expected_menu_date_tokens(dt: datetime) -> List[str]:
        """Return day/month tokens that appear in Betfair menu_hint (e.g. '25th Nov')."""
        day = dt.day
        suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        mon = dt.strftime("%b")
        return [f"{day}{suffix} {mon}".lower(), f"{day} {mon}".lower()]

    def fetch_daily_bsp(self, date_str: str) -> Dict[str, Dict[str, Any]]:
        """
        Return lookup map keyed by normalized horse name -> {win_bsp, place_bsp, win_lose, ppwap, menu_hint}.
        Betfair names daily AU promo CSVs by UTC publish date (typically race_date + 1 day, e.g.
        dwbfpricesauswin26112026.csv holds '25th Nov' AU races). Check race_date+1 first, then race_date.
        """
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        expected_tokens = self._expected_menu_date_tokens(dt)
        candidate_dates = [dt + timedelta(days=1), dt]

        lookup: Dict[str, Dict[str, Any]] = {}
        bf_headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/csv,text/plain,*/*",
        }

        matched_ddmmyyyy: Optional[str] = None
        win_csv = None
        for cand_dt in candidate_dates:
            ddmmyyyy = cand_dt.strftime("%d%m%Y")
            win_url = f"{BETFAIR_BSP_BASE}/dwbfpricesauswin{ddmmyyyy}.csv"
            try:
                text = self.http.get_text(win_url, headers=bf_headers, allow_404=True)
            except Exception as exc:
                logger.info("Betfair Win BSP CSV unavailable/unpublished for %s (%s): %s", date_str, win_url, exc)
                continue
            if not text:
                continue
            # Verify the CSV actually contains rows for date_str (e.g. "25th Nov")
            lower_sample = text[:8000].lower()
            if any(tok in lower_sample for tok in expected_tokens) or cand_dt == candidate_dates[-1]:
                win_csv = text
                matched_ddmmyyyy = ddmmyyyy
                break

        if win_csv and matched_ddmmyyyy:
            for row in csv.DictReader(io.StringIO(win_csv)):
                sel_name = normalize_horse_name(row.get("selection_name") or "")
                if not sel_name:
                    continue
                try:
                    bsp = float(row.get("bsp") or 0)
                except ValueError:
                    bsp = 0.0
                if bsp <= 0:
                    continue
                lookup[sel_name] = {
                    "selection_name": row.get("selection_name") or "",
                    "menu_hint": row.get("menu_hint") or "",
                    "event_name": row.get("event_name") or "",
                    "win_bsp": round(bsp, 2),
                    "win_lose": int(row.get("win_lose") or 0),
                    "ppwap": round(float(row.get("ppwap") or 0), 2) if row.get("ppwap") else None,
                    "place_bsp": None,
                }
            logger.info("Loaded %d Betfair Win BSP records for %s (file %s)", len(lookup), date_str, matched_ddmmyyyy)
        else:
            logger.info("Betfair Win BSP CSV not yet published/accessible for %s", date_str)

        place_csv = None
        place_ddmmyyyy = matched_ddmmyyyy or (dt + timedelta(days=1)).strftime("%d%m%Y")
        place_url = f"{BETFAIR_BSP_BASE}/dwbfpricesausplace{place_ddmmyyyy}.csv"
        try:
            place_csv = self.http.get_text(place_url, headers=bf_headers, allow_404=True)
        except Exception as exc:
            logger.info("Betfair Place BSP CSV unavailable/unpublished for %s (%s): %s", date_str, place_url, exc)

        if place_csv:
            place_hits = 0
            for row in csv.DictReader(io.StringIO(place_csv)):
                sel_name = normalize_horse_name(row.get("selection_name") or "")
                if not sel_name:
                    continue
                try:
                    pbsp = float(row.get("bsp") or 0)
                except ValueError:
                    pbsp = 0.0
                if pbsp <= 0:
                    continue
                if sel_name in lookup:
                    lookup[sel_name]["place_bsp"] = round(pbsp, 2)
                else:
                    lookup[sel_name] = {
                        "selection_name": row.get("selection_name") or "",
                        "menu_hint": row.get("menu_hint") or "",
                        "event_name": row.get("event_name") or "",
                        "win_bsp": None,
                        "win_lose": 0,
                        "ppwap": None,
                        "place_bsp": round(pbsp, 2),
                    }
                place_hits += 1
            logger.info("Loaded %d Betfair Place BSP records for %s", place_hits, date_str)

        return lookup
