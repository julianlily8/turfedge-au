from __future__ import annotations

import html as html_lib
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from pipeline.src.http_client import PoliteHttpClient
from pipeline.src.sources.betfair_bsp import normalize_horse_name

logger = logging.getLogger(__name__)

FREEFIELDS_BASE = "https://racing.racingnsw.com.au/FreeFields"
LADBROKES_BLOG_INDEX = "https://www.ladbrokes.com.au/blog/racing-tips/"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

VET_CONCERN_KEYWORDS = (
    "lame",
    "lameness",
    "bled",
    "bleed",
    "stand-down",
    "stood down",
    "embargo",
    "tendon",
    "cardiac",
    "arrhythmia",
    "respiratory",
    "epistaxis",
    "tied up",
    "sore",
    "swelling",
    "abnormality",
    "abnormalities detected",
    "veterinary clearance",
    "vet clearance",
    "required to trial",
    "must trial",
)

VET_CLEAN_KEYWORDS = (
    "no abnormalities",
    "rev no abs",
    "passed fit",
    "cleared to race",
    "has trialed",
    "has trialled",
)


def _strip_tags(raw: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", raw or "")
    txt = html_lib.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def _parse_freefields_key_date(key_str: str) -> Optional[datetime]:
    """Parse FreeFields date prefix like '2026Nov24' into a datetime.date-aligned datetime."""
    m = re.match(r"^(\d{4}[A-Za-z]{3}\d{2})", (key_str or "").strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%b%d")
    except ValueError:
        return None


def _format_freefields_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%Y%b%d")


def parse_pre_stewards_html(html_text: str, cutoff_date_str: Optional[str] = None) -> Dict[str, Dict[str, List[str]]]:
    """
    Parse Racing NSW PreStewardsComments.aspx HTML into a lookup keyed by normalized horse name:
    { norm_name: { "stewards_notes": [...], "vet_notes": [...], "vet_clean_notes": [...], "trial_notes": [...] } }
    Strictly filters out any incident dated >= cutoff_date_str when cutoff_date_str is set.
    """
    if not html_text:
        return {}
    cutoff_dt = datetime.strptime(cutoff_date_str, "%Y-%m-%d").date() if cutoff_date_str else None
    out: Dict[str, Dict[str, List[str]]] = {}

    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html_text, re.S | re.I):
        cells = [_strip_tags(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if len(cells) < 5:
            continue
        # Expected 5 columns: [runner_num, horse_name, incident_date (D/MM/YYYY), venue, comment]
        num_str, horse_raw, date_raw, venue_raw, comment = cells[0], cells[1], cells[2], cells[3], cells[4]
        if not num_str.isdigit() or not horse_raw or len(comment) < 12:
            continue

        # Respect blind cutoff date (D/M/YYYY)
        if cutoff_dt and date_raw:
            try:
                inc_dt = datetime.strptime(date_raw.strip(), "%d/%m/%Y").date()
                if inc_dt >= cutoff_dt:
                    continue
            except ValueError:
                pass

        norm = normalize_horse_name(horse_raw)
        if not norm:
            continue
        bucket = out.setdefault(
            norm,
            {"stewards_notes": [], "vet_notes": [], "vet_clean_notes": [], "trial_notes": []},
        )
        formatted = f"[{date_raw} {venue_raw}] {comment}"
        bucket["stewards_notes"].append(formatted)

        cl = comment.lower()
        if any(kw in cl for kw in VET_CONCERN_KEYWORDS):
            # Avoid false-positive on "no abnormalities" matching "abnormality"
            if any(clean_kw in cl for clean_kw in VET_CLEAN_KEYWORDS) and not any(
                sev in cl for sev in ("lame", "bled", "bleed", "stand-down", "stood down", "tendon", "cardiac", "required to trial", "must trial")
            ):
                bucket["vet_clean_notes"].append(formatted)
            else:
                bucket["vet_notes"].append(formatted)
        elif "vet" in cl or any(clean_kw in cl for clean_kw in VET_CLEAN_KEYWORDS):
            bucket["vet_clean_notes"].append(formatted)

        if "trial" in cl or "jumpout" in cl:
            bucket["trial_notes"].append(formatted)

    return out


def parse_trial_results_html(html_text: str, venue: str, trial_date_str: str) -> Dict[str, List[Dict[str, Any]]]:
    """Parse Racing NSW FreeFields Results.aspx?...Trial HTML rows into normalized horse trial records."""
    if not html_text:
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}

    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html_text, re.S | re.I):
        cells = [_strip_tags(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        # Typical row: ['', '1', '7', 'SIR DELIUS (GB)', 'Trainer', 'Jockey', '4.88', '0']
        if len(cells) < 6:
            continue
        pos_raw = cells[1].strip() if cells[0] == "" else cells[0].strip()
        horse_raw = cells[3].strip() if cells[0] == "" else cells[2].strip()
        margin_raw = cells[6].strip() if (cells[0] == "" and len(cells) > 6) else (cells[5].strip() if len(cells) > 5 else "")

        if not pos_raw.isdigit() or not horse_raw or len(horse_raw) < 3:
            continue
        pos = int(pos_raw)
        if pos <= 0 or pos > 20:
            continue
        try:
            margin_val = float(margin_raw) if margin_raw else 0.0
        except ValueError:
            margin_val = 0.0

        norm = normalize_horse_name(horse_raw)
        if not norm:
            continue
        out.setdefault(norm, []).append(
            {
                "position": pos,
                "margin": round(margin_val, 2),
                "venue": venue,
                "date": trial_date_str,
                "source": "Racing NSW FreeFields Trial",
            }
        )
    return out


def parse_ladbrokes_blog_notes(html_text: str, runner_names: List[str]) -> Dict[str, List[str]]:
    """Extract paragraph mentions of runners from Ladbrokes daily racing tips blog HTML."""
    if not html_text or not runner_names:
        return {}
    paras = [_strip_tags(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", html_text, re.S | re.I)]
    good_paras = [p for p in paras if 35 <= len(p) <= 900]

    out: Dict[str, List[str]] = {}
    for name in runner_names:
        clean = (name or "").strip()
        if len(clean) < 4:
            continue
        norm = normalize_horse_name(clean)
        pat = re.compile(rf"\b{re.escape(clean)}\b", re.IGNORECASE)
        hits = [p for p in good_paras if pat.search(p)]
        if hits:
            out[norm] = hits[:2]
    return out


class NswFreeFieldsSource:
    """
    Zero-key Racing NSW FreeFields + Ladbrokes Daily Tips enrichment client.
    - Fetches 21-day pre-race NSW Barrier Trial results (strictly < target_date).
    - Fetches NSW meeting PreStewardsComments (stewards excuses + vet/injury/clearance flags).
    - Fetches Ladbrokes daily racing tips blog article matching target_date (graceful fallback).
    """

    def __init__(self, http: PoliteHttpClient) -> None:
        self.http = http

    def fetch_recent_trials(
        self,
        target_date_str: str,
        lookback_days: int = 21,
        max_trial_meetings: int = 6,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch NSW Barrier Trials dated strictly BEFORE target_date_str within lookback_days."""
        target_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        min_dt = target_dt - timedelta(days=lookback_days)
        trials_by_horse: Dict[str, List[Dict[str, Any]]] = {}

        try:
            cal_html = self.http.get_text(
                f"{FREEFIELDS_BASE}/Calendar_Results.aspx",
                headers=BROWSER_HEADERS,
                allow_404=True,
            )
        except Exception as exc:
            logger.warning("Racing NSW Calendar_Results.aspx unavailable: %s", exc)
            return trials_by_horse

        if not cal_html:
            return trials_by_horse

        # Extract single-quoted or double-quoted Results.aspx?Key=...Trial keys
        raw_keys = re.findall(r"Results\.aspx\?Key=([^'\"&<>]+)", cal_html, re.I)
        candidates: List[Tuple[datetime, str, str]] = []
        seen = set()
        priority_tracks = ("warwick farm", "randwick", "rosehill", "hawkesbury", "wyong", "gosford", "newcastle", "kembla", "canterbury")

        for k in raw_keys:
            if "trial" not in k.lower() or k in seen:
                continue
            seen.add(k)
            k_dt = _parse_freefields_key_date(k)
            if not k_dt:
                continue
            d = k_dt.date()
            if min_dt <= d < target_dt:
                parts = k.split(",")
                venue = parts[2].strip() if len(parts) >= 3 else "NSW"
                candidates.append((k_dt, venue, k))

        # Prioritize major Sydney/Provincial trial tracks & most recent dates
        candidates.sort(
            key=lambda x: (
                any(pt in x[1].lower() for pt in priority_tracks),
                x[0],
            ),
            reverse=True,
        )

        fetched_count = 0
        for k_dt, venue, key_str in candidates[:max_trial_meetings]:
            url = f"{FREEFIELDS_BASE}/Results.aspx?Key={quote(key_str, safe=',')}"
            try:
                thtml = self.http.get_text(url, headers=BROWSER_HEADERS, allow_404=True)
                if not thtml:
                    continue
                parsed = parse_trial_results_html(thtml, venue=venue, trial_date_str=k_dt.strftime("%Y-%m-%d"))
                for norm_name, recs in parsed.items():
                    trials_by_horse.setdefault(norm_name, []).extend(recs)
                fetched_count += 1
            except Exception as exc:
                logger.warning("Failed fetching NSW trial %s: %s", key_str, exc)

        # Sort each horse's trials newest-first
        for norm_name in trials_by_horse:
            trials_by_horse[norm_name].sort(key=lambda r: r.get("date", ""), reverse=True)

        logger.info(
            "Racing NSW FreeFields Trials (< %s, %dd): fetched %d trial meeting(s), indexed %d unique horses.",
            target_date_str,
            lookback_days,
            fetched_count,
            len(trials_by_horse),
        )
        return trials_by_horse

    def fetch_meeting_pre_stewards(
        self,
        date_str: str,
        meeting_name: str,
        state: str,
        aliases: Optional[List[str]] = None,
    ) -> Tuple[bool, Dict[str, Dict[str, List[str]]]]:
        """
        Fetch Racing NSW PreStewardsComments.aspx for an NSW meeting.
        Returns (was_checked, lookup_by_norm_horse_name).
        """
        if (state or "").upper() != "NSW":
            return False, {}

        ff_date = _format_freefields_date(date_str)
        names_to_try = list(dict.fromkeys([meeting_name] + list(aliases or [])))

        for mname in names_to_try:
            key = f"{ff_date},NSW,{mname}"
            url = f"{FREEFIELDS_BASE}/PreStewardsComments.aspx?Key={quote(key, safe=',')}"
            try:
                html_text = self.http.get_text(url, headers=BROWSER_HEADERS, allow_404=True)
                if html_text and mname.lower() in html_text.lower() and "Pre-Stewards" in html_text:
                    parsed = parse_pre_stewards_html(html_text, cutoff_date_str=date_str)
                    logger.info(
                        "Racing NSW PreStewardsComments [%s %s]: matched %d horse(s) with stewards/vet history.",
                        date_str,
                        mname,
                        len(parsed),
                    )
                    return True, parsed
            except Exception as exc:
                logger.warning("PreStewardsComments unavailable for %s: %s", key, exc)

        return False, {}

    def fetch_ladbrokes_daily_blog_html(self, target_date_str: str) -> str:
        """Fetch the Ladbrokes daily racing tips article matching target_date_str (graceful fallback)."""
        dt = datetime.strptime(target_date_str, "%Y-%m-%d")
        day_num = dt.day
        month_name = dt.strftime("%B").lower()
        ordinal_suffix = "th" if 11 <= day_num <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day_num % 10, "th")
        date_slug_token = f"{day_num}{ordinal_suffix}-{month_name}"

        try:
            index_html = self.http.get_text(LADBROKES_BLOG_INDEX, headers=BROWSER_HEADERS, allow_404=True)
            if not index_html:
                return ""
            links = list(dict.fromkeys(re.findall(r'href="(https://www\.ladbrokes\.com\.au/blog/\d{4}/[^"]+)"', index_html)))
            # Match daily-racing-tips article for target date
            matching = [
                u for u in links
                if "daily-racing-tips" in u.lower() and date_slug_token in u.lower()
            ]
            if not matching:
                return ""
            article_url = matching[0]
            art_html = self.http.get_text(article_url, headers=BROWSER_HEADERS, allow_404=True) or ""
            logger.info("Ladbrokes Daily Tips Blog matched for %s: %s (%d bytes)", target_date_str, article_url, len(art_html))
            return art_html
        except Exception as exc:
            logger.warning("Ladbrokes Blog Racing Tips unavailable for %s: %s", target_date_str, exc)
            return ""
