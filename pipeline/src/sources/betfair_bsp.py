from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

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

    def fetch_daily_bsp(self, date_str: str) -> Dict[str, Dict[str, Any]]:
        """
        Return lookup map keyed by normalized horse name -> {win_bsp, place_bsp, win_lose, ppwap, menu_hint}.
        Betfair filenames use DDMMYYYY (e.g. dwbfpricesauswin24112026.csv).
        """
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        ddmmyyyy = dt.strftime("%d%m%Y")
        win_url = f"{BETFAIR_BSP_BASE}/dwbfpricesauswin{ddmmyyyy}.csv"
        place_url = f"{BETFAIR_BSP_BASE}/dwbfpricesausplace{ddmmyyyy}.csv"

        lookup: Dict[str, Dict[str, Any]] = {}

        win_csv = self.http.get_text(win_url, allow_404=True)
        if win_csv:
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
            logger.info("Loaded %d Betfair Win BSP records for %s", len(lookup), date_str)
        else:
            logger.info("Betfair Win BSP CSV not yet published for %s (%s)", date_str, win_url)

        place_csv = self.http.get_text(place_url, allow_404=True)
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
