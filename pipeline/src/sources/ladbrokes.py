from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pipeline.src.http_client import PoliteHttpClient

logger = logging.getLogger(__name__)

LADBROKES_BASE = "https://api.ladbrokes.com.au/affiliates/v1/racing"
TRACKS_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "au_tracks.json"


def load_au_tracks() -> Dict[str, Dict[str, Any]]:
    raw = json.loads(TRACKS_CONFIG_PATH.read_text(encoding="utf-8"))
    return raw.get("tracks", {})


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def match_track(meeting_name: str, tracks: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Match a Ladbrokes meeting name against the Australian Metro & Provincial track directory."""
    tracks = tracks or load_au_tracks()
    norm = meeting_name.strip().lower()
    for slug, info in tracks.items():
        if slug == slugify(norm):
            return slug, info
        for alias in info.get("aliases", []):
            a_norm = alias.strip().lower()
            if norm == a_norm or a_norm in norm or norm in a_norm:
                return slug, info
    return None


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(str(val).replace("$", "").replace(",", "").replace("kg", "").strip())
    except (ValueError, TypeError):
        return default


def _parse_allowance(runner: Dict[str, Any]) -> float:
    raw = runner.get("allowance_weight")
    if raw:
        return _safe_float(raw, 0.0)
    app = str(runner.get("apprentice_indicator") or "")
    m = re.search(r"(\d+(?:\.\d+)?)", app)
    return float(m.group(1)) if m else 0.0


def normalize_track_condition_band(condition: str) -> str:
    c = (condition or "").lower()
    if "heavy" in c:
        return "Heavy"
    if "soft" in c:
        return "Soft"
    if "firm" in c:
        return "Firm"
    if "synth" in c or "poly" in c:
        return "Synthetic"
    return "Good"


def normalize_distance_band(distance_m: int) -> str:
    if distance_m <= 1200:
        return "Sprint"
    if distance_m <= 1600:
        return "Middle"
    return "Staying"


class LadbrokesSource:
    """Zero-key public Australian thoroughbred racing client (Ladbrokes Affiliates v1)."""

    def __init__(self, http: PoliteHttpClient) -> None:
        self.http = http
        self.tracks = load_au_tracks()

    def fetch_aus_meetings(self, date_str: str, include_country: bool = False) -> List[Dict[str, Any]]:
        """Fetch AUS thoroughbred meetings for YYYY-MM-DD, filtered to Metro & Provincial by default."""
        url = f"{LADBROKES_BASE}/meetings"
        params = {
            "category": "T",
            "date_from": date_str,
            "date_to": date_str,
            "enc": "json",
        }
        payload = self.http.get_json(url, params=params) or {}
        raw_meetings = payload.get("data", {}).get("meetings", []) or []

        filtered: List[Dict[str, Any]] = []
        for m in raw_meetings:
            if (m.get("country") or "").upper() != "AUS":
                continue
            name = (m.get("name") or "").strip()
            matched = match_track(name, self.tracks)
            if matched:
                slug, track_meta = matched
                tier = track_meta["tier"]
            else:
                if not include_country:
                    continue
                slug = slugify(name)
                track_meta = {
                    "name": name,
                    "state": m.get("state", "AUS"),
                    "tier": "Country",
                    "lat": -33.8688,
                    "lon": 151.2093,
                    "bom_geohash": "r3gx2f",
                    "turning": "Clockwise",
                    "home_straight_m": 350,
                    "home_straight_bearing": 0,
                    "historical_bias": "Standard Australian country circuit.",
                }
                tier = "Country"

            races_summary = []
            for r in m.get("races", []) or []:
                races_summary.append(
                    {
                        "race_id": r.get("id"),
                        "race_number": int(r.get("race_number") or 0),
                        "name": r.get("name") or f"Race {r.get('race_number')}",
                        "start_time": r.get("start_time"),
                        "distance": int(r.get("distance") or 0),
                        "track_condition": r.get("track_condition") or m.get("track_condition") or "Good4",
                        "weather": r.get("weather") or "",
                        "status": r.get("status") or "Open",
                    }
                )
            races_summary.sort(key=lambda x: x["race_number"])

            filtered.append(
                {
                    "meeting_id": m.get("meeting"),
                    "meeting_slug": slug,
                    "date": date_str,
                    "name": name,
                    "display_name": track_meta.get("name", name),
                    "state": m.get("state") or track_meta.get("state", "AUS"),
                    "tier": tier,
                    "track_condition": m.get("track_condition") or "Good4",
                    "track_condition_band": normalize_track_condition_band(m.get("track_condition") or "Good4"),
                    "quaddie_races": [int(x) for x in (m.get("quaddie") or []) if str(x).isdigit()],
                    "track_meta": track_meta,
                    "races": races_summary,
                }
            )

        logger.info("Found %d AUS Metro/Provincial meetings for %s", len(filtered), date_str)
        return filtered

    def fetch_race_event(self, race_id: str) -> Dict[str, Any]:
        """Fetch detailed race card, runners, odds, flucs, speedmap, past runs, results & dividends."""
        url = f"{LADBROKES_BASE}/events/{race_id}"
        payload = self.http.get_json(url, params={"enc": "json"}) or {}
        data = payload.get("data") or {}
        race = data.get("race") or {}
        runners_raw = data.get("runners") or []
        results_raw = data.get("results") or []
        dividends_raw = data.get("dividends") or []

        distance = int(race.get("distance") or 0)
        track_cond = race.get("track_condition") or "Good4"

        runners: List[Dict[str, Any]] = []
        for r in runners_raw:
            odds = r.get("odds") or {}
            flucs = [float(x) for x in (r.get("flucs") or []) if _safe_float(x) > 0]
            fixed_win = _safe_float(odds.get("fixed_win"), 0.0)
            fixed_place = _safe_float(odds.get("fixed_place"), 0.0)
            opening_win = flucs[0] if flucs else fixed_win

            weight_obj = r.get("weight") or {}
            allocated_w = _safe_float(weight_obj.get("allocated"), 57.0)
            total_w = _safe_float(weight_obj.get("total"), allocated_w)
            claim_kg = _parse_allowance(r)

            speedmap = r.get("speedmap") or {}
            past_perfs_raw = r.get("past_performances")
            jockey_perfs_raw = r.get("jockey_past_performances")
            past_perfs_list = past_perfs_raw if isinstance(past_perfs_raw, list) else []
            past_perfs_stats = past_perfs_raw if isinstance(past_perfs_raw, dict) else {}
            jockey_perfs_list = jockey_perfs_raw if isinstance(jockey_perfs_raw, list) else []
            jockey_perfs_stats = jockey_perfs_raw if isinstance(jockey_perfs_raw, dict) else {}

            # Extract any genuine text fields & form indicators without inventing anything
            form_comment = (r.get("form_comment") or r.get("comment") or r.get("overview") or "").strip()
            raw_indicators = r.get("form_indicators") or []
            form_indicators = [
                {"name": str(fi.get("name") or ""), "group": str(fi.get("group") or ""), "negative": bool(fi.get("negative", False))}
                for fi in raw_indicators
                if isinstance(fi, dict) and fi.get("name")
            ]
            stewards_notes = self._extract_stewards_from_past_runs(past_perfs_list)
            trial_runs = self._extract_trials_from_past_runs(past_perfs_list)
            vet_notes = self._extract_vet_notes(form_comment, stewards_notes)

            runners.append(
                {
                    "entrant_id": r.get("entrant_id") or "",
                    "runner_number": int(r.get("runner_number") or 0),
                    "name": (r.get("name") or "").strip(),
                    "barrier": int(r.get("barrier") or 0),
                    "is_scratched": bool(r.get("is_scratched", False)),
                    "jockey": (r.get("jockey") or "TBA").strip(),
                    "trainer": (r.get("trainer_name") or "Unknown").strip(),
                    "apprentice_claim_kg": claim_kg,
                    "weight_allocated": allocated_w,
                    "weight_carried": max(48.0, total_w - claim_kg) if total_w == allocated_w and claim_kg > 0 else total_w,
                    "gear": (r.get("gear") or "").strip(),
                    "sire": (r.get("sire") or "").strip(),
                    "dam": (r.get("dam") or "").strip(),
                    "breeding": (r.get("breeding") or "").strip(),
                    "age": int(r.get("age") or 0),
                    "sex": (r.get("sex") or "").strip(),
                    "prize_money": _safe_float(r.get("prize_money"), 0.0),
                    "last_twenty_starts": (r.get("last_twenty_starts") or "").strip(),
                    "silk_url": r.get("silk_url_64x64") or r.get("silk_url_128x128") or "",
                    "favourite": bool(r.get("favourite", False)),
                    "mover": bool(r.get("mover", False)),
                    "opening_win": opening_win,
                    "live_win": fixed_win,
                    "live_place": fixed_place,
                    "flucs": flucs,
                    "speedmap_label": (speedmap.get("label") or "").strip(),
                    "settling_lengths": _safe_float(speedmap.get("settling_lengths"), -1.0),
                    "form_comment": form_comment,
                    "form_indicators": form_indicators,
                    "stewards_notes": stewards_notes,
                    "vet_notes": vet_notes,
                    "vet_clean_notes": [],
                    "trial_runs": trial_runs,
                    "blog_notes": [],
                    "nsw_freefields_checked": False,
                    "past_performances": past_perfs_list[:8],
                    "past_performance_stats": past_perfs_stats,
                    "jockey_past_performances": jockey_perfs_list[:10],
                    "jockey_stats": jockey_perfs_stats,
                }
            )

        runners.sort(key=lambda x: x["runner_number"])

        parsed_results = [
            {
                "position": int(res.get("position") or 0),
                "runner_number": int(res.get("runner_number") or 0),
                "name": (res.get("name") or "").strip(),
                "barrier": int(res.get("barrier") or 0),
                "margin_length": _safe_float(res.get("margin_length"), 0.0),
                "entrant_id": res.get("entrant_id") or "",
            }
            for res in results_raw
            if int(res.get("position") or 0) > 0
        ]
        parsed_results.sort(key=lambda x: x["position"])

        parsed_dividends = self._parse_dividends(dividends_raw)

        return {
            "race_id": race_id,
            "meeting_id": race.get("meeting_id") or "",
            "meeting_name": race.get("meeting_name") or race.get("display_meeting_name") or "",
            "race_number": int(race.get("race_number") or 0),
            "name": race.get("description") or f"Race {race.get('race_number')}",
            "status": race.get("status") or "Open",
            "advertised_start": race.get("advertised_start_string") or "",
            "distance": distance,
            "distance_band": normalize_distance_band(distance),
            "track_condition": track_cond,
            "track_condition_band": normalize_track_condition_band(track_cond),
            "rail_position": (race.get("rail_position") or "True").strip(),
            "track_direction": (race.get("track_direction") or "").strip(),
            "track_home_straight": int(_safe_float(race.get("track_home_straight"), 0)),
            "weather_code": (race.get("weather") or "").strip(),
            "race_comment": (race.get("comment") or "").strip(),
            "prize_money": _safe_float(
                (race.get("prize_monies") or {}).get("total_value")
                if isinstance(race.get("prize_monies"), dict)
                else race.get("prize_monies"),
                0.0,
            ),
            "positions_paid": int(race.get("positions_paid") or 3),
            "runners": runners,
            "results": parsed_results,
            "dividends": parsed_dividends,
        }

    @staticmethod
    def _extract_stewards_from_past_runs(past_perfs: List[Dict[str, Any]]) -> List[str]:
        notes: List[str] = []
        for pp in past_perfs[:5]:
            if not isinstance(pp, dict):
                continue
            for key in ("stewards_comment", "stewards_report", "comment", "notes"):
                val = (pp.get(key) or "").strip() if isinstance(pp.get(key), str) else ""
                if val and len(val) > 8:
                    notes.append(val)
        return notes

    @staticmethod
    def _extract_trials_from_past_runs(past_perfs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        trials: List[Dict[str, Any]] = []
        for pp in past_perfs[:6]:
            if not isinstance(pp, dict):
                continue
            cls = str(pp.get("class") or pp.get("race_class") or pp.get("name") or "").lower()
            if "trial" in cls or "jumpout" in cls or "barrier" in cls or bool(pp.get("is_trial")):
                trials.append(pp)
        return trials

    @staticmethod
    def _extract_vet_notes(form_comment: str, stewards_notes: List[str]) -> List[str]:
        vet_keywords = ("vet", "lame", "bleed", "injury", "surgery", "tendon", "respiratory", "cardiac", "tied up", "sore")
        combined = [form_comment] + stewards_notes
        hits = [t for t in combined if t and any(kw in t.lower() for kw in vet_keywords)]
        return hits

    @staticmethod
    def _parse_dividends(dividends_raw: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Extract Best Tote Win/Place and official Exacta, Trifecta, First4, Quaddie dividends."""
        win_divs: Dict[int, List[float]] = {}
        place_divs: Dict[int, List[float]] = {}
        exotics: Dict[str, List[float]] = {
            "exacta": [],
            "trifecta": [],
            "first4": [],
            "quaddie": [],
        }
        for d in dividends_raw:
            if not isinstance(d, dict):
                continue
            pname_raw = (d.get("product_name") or d.get("type") or "").strip().lower()
            pname_norm = re.sub(r"[^a-z0-9]+", "_", pname_raw).strip("_")
            val = _safe_float(d.get("dividend"), 0.0)
            if val <= 0:
                continue
            positions = d.get("positions") or []
            if "win" in pname_raw and "double" not in pname_raw and positions:
                rn = int(positions[0].get("runner_number") or 0)
                if rn:
                    win_divs.setdefault(rn, []).append(val)
            elif "place" in pname_raw and positions:
                rn = int(positions[0].get("runner_number") or 0)
                if rn:
                    place_divs.setdefault(rn, []).append(val)
            elif "exacta" in pname_norm:
                exotics["exacta"].append(val)
            elif "trifecta" in pname_norm:
                exotics["trifecta"].append(val)
            elif (
                pname_norm in ("first_4", "first4", "first_four", "firstfour", "superfecta")
                or ("first" in pname_raw and ("4" in pname_raw or "four" in pname_raw))
                or "superfecta" in pname_raw
            ):
                exotics["first4"].append(val)
            elif "quaddie" in pname_norm or "quadrella" in pname_norm:
                exotics["quaddie"].append(val)

        return {
            "best_tote_win": {str(k): round(max(v), 2) for k, v in win_divs.items()},
            "best_tote_place": {str(k): round(max(v), 2) for k, v in place_divs.items()},
            "exacta": round(max(exotics["exacta"]), 2) if exotics["exacta"] else None,
            "trifecta": round(max(exotics["trifecta"]), 2) if exotics["trifecta"] else None,
            "first4": round(max(exotics["first4"]), 2) if exotics["first4"] else None,
            "quaddie": round(max(exotics["quaddie"]), 2) if exotics["quaddie"] else None,
        }
