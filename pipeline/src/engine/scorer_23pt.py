from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pipeline.src.sources.betfair_bsp import normalize_horse_name

WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "config" / "factor_weights.json"
NEUTRAL_SCORE = 5.0
NO_DATA_LABEL = "No data available"

# Factors where missing raw text MUST strictly remain 5.0 / "No data available"
STRICT_TEXT_FACTORS = {
    "recent_trials",
    "injury_history",
    "stewards_comments",
    "trainer_jockey_commentary",
    "youtube_transcripts",
}

WET_SIRES = {
    "so you think", "savabeel", "proisir", "ocean park", "d confiable", "shamus award",
    "dunn", "reliable man", "shocking", "tarzino", "almanzor", "pierro", "street boss",
    "toronado", "hellbent", "all too hard", "starspangledbanner", "written tycoon",
}
STAYING_SIRES = {
    "savabeel", "so you think", "camelot", "frankel", "galileo", "teofilo", "tarzino",
    "shocking", "reliable man", "dundeel", "ocean park", "almanzor", "pierro", "tosen stardon",
}
SPRINT_SIRES = {
    "i am invincible", "snitzel", "exceed and excel", "extreme choice", "capitalist",
    "written tycoon", "zoustar", "spirit of boom", "russian revolution", "better than ready",
    "hellbent", "deep field", "star witness", "flying artie", "harry angel",
}

ELITE_JOCKEYS = {
    "james mcdonald", "jamie kah", "craig williams", "blake shinn", "damian lane",
    "nash rawiller", "tim clark", "jason collett", "tyler schiller", "tommy berry",
    "kerrin mcevoy", "mark zahra", "ethan brown", "ben melham", "luke nolen",
    "william pike", "chris parnham", "ryan maloney", "angela jones", "james orman",
    "jake toeroek", "lachlan neindorf", "jye mcneil", "daniel stackhouse",
}

ELITE_TRAINERS = {
    "chris waller", "ciaron maher", "gai waterhouse", "adrian bott", "peter moody",
    "katherine coleman", "james cummings", "ben, will & jd hayes", "hayes", "bjorn baker",
    "annabel", "tony gollan", "mick price", "michael kent", "anthony & sam freedman",
    "graham begg", "john o'shea", "michael freedman", "grant & alana williams",
    "joe pride", "kris lees", "robert heathcote", "richard & chantelle jolly",
}


def load_factor_weights() -> Dict[str, Any]:
    return json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))


def clamp(val: float, lo: float = 0.5, hi: float = 10.0) -> float:
    return round(max(lo, min(hi, val)), 2)


def compute_market_fluc(opening_win: float, live_win: float) -> Dict[str, Any]:
    """Compute implied probability firming/drifting delta against opening price."""
    if opening_win <= 1.0 or live_win <= 1.0:
        return {
            "opening_win": opening_win,
            "live_win": live_win,
            "opening_implied_prob": 0.0,
            "live_implied_prob": 0.0,
            "implied_prob_delta_pct": 0.0,
            "fluc_pct": 0.0,
            "direction": "Steady",
        }
    p_open = 1.0 / opening_win
    p_live = 1.0 / live_win
    delta_pp = (p_live - p_open) * 100.0
    rel_pct = ((p_live - p_open) / p_open) * 100.0

    if rel_pct >= 6.0 or delta_pp >= 1.8:
        direction = "Firmer"
    elif rel_pct <= -6.0 or delta_pp <= -1.8:
        direction = "Drifter"
    else:
        direction = "Steady"

    return {
        "opening_win": round(opening_win, 2),
        "live_win": round(live_win, 2),
        "opening_implied_prob": round(p_open * 100.0, 2),
        "live_implied_prob": round(p_live * 100.0, 2),
        "implied_prob_delta_pct": round(delta_pp, 2),
        "fluc_pct": round(rel_pct, 1),
        "direction": direction,
    }


def _extract_runner_speedmap_fields(runner: Dict[str, Any]) -> Tuple[str, Optional[float]]:
    """Read running-style label and numeric settling_position across all Ladbrokes speedmap keys."""
    if not isinstance(runner, dict):
        return "", None

    label = ""
    settling_pos: Optional[float] = None

    nested_sm = runner.get("speedmap")
    if isinstance(nested_sm, dict):
        label = str(nested_sm.get("label") or nested_sm.get("pace_style") or nested_sm.get("running_style") or "").strip()
        raw_sp = nested_sm.get("settling_position") or nested_sm.get("position")
        if raw_sp is not None and raw_sp != "":
            try:
                val = float(raw_sp)
                if val > 0:
                    settling_pos = val
            except (TypeError, ValueError):
                pass
    elif isinstance(nested_sm, str) and nested_sm.strip():
        label = nested_sm.strip()

    for key in ("speedmap_label", "pace_style", "running_style"):
        val = runner.get(key)
        if isinstance(val, str) and val.strip():
            label = val.strip()
            break

    if settling_pos is None:
        raw_sp = runner.get("settling_position")
        if raw_sp is not None and raw_sp != "":
            try:
                val = float(raw_sp)
                if val > 0:
                    settling_pos = val
            except (TypeError, ValueError):
                pass

    return label, settling_pos


def classify_runner_pace_style(runner: Dict[str, Any]) -> str:
    """
    Normalize Ladbrokes running-style labels and numeric settling_position into:
      - 'on_pace': leader, pace, on pace, on-pace, off pace, off-pace, handy, front, mid-front (or settling_position <= 3)
      - 'closer':  backmarker, off midfield, off-midfield, closer, rear, tail, back (or settling_position >= 6)
      - 'neutral': midfield (or unknown / settling_position 4..5)
    """
    label, settling_pos = _extract_runner_speedmap_fields(runner)
    norm = re.sub(r"\s+", " ", label.lower().strip())

    if norm:
        # Check 'off midfield' / 'off-midfield' BEFORE 'midfield' so it classifies as closer
        if norm in ("backmarker", "off midfield", "off-midfield", "closer", "rear", "tail", "back") or any(
            k in norm for k in ("backmarker", "off midfield", "off-midfield", "closer", "rear", "tail", "back")
        ):
            return "closer"
        if norm in ("leader", "pace", "on pace", "on-pace", "off pace", "off-pace", "handy", "front", "mid-front") or any(
            k in norm for k in ("lead", "pace", "handy", "front")
        ):
            return "on_pace"
        if "midfield" in norm or norm == "mid":
            return "neutral"

    if settling_pos is not None:
        if settling_pos <= 3:
            return "on_pace"
        if settling_pos >= 6:
            return "closer"

    return "neutral"


def _is_leader_runner(runner: Dict[str, Any]) -> bool:
    label, settling_pos = _extract_runner_speedmap_fields(runner)
    norm = label.lower().strip()
    if any(k in norm for k in ("lead", "front")):
        return True
    if not norm and settling_pos is not None and settling_pos <= 1.5:
        return True
    return False


def _resolve_race_winner(race: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], int]:
    """
    Resolve (winner_result_meta, winner_runner_obj, active_field_size) from a finished race document,
    handling both pre-score (`runners`) and Firestore persisted (`scored_runners`) layouts.
    """
    all_runners = race.get("scored_runners") or race.get("runners") or []
    active_runners = [r for r in all_runners if isinstance(r, dict) and not r.get("is_scratched")]
    field_sz = max(len(active_runners), len(all_runners), 1)

    results = race.get("results") or []
    winner_meta: Optional[Dict[str, Any]] = None
    win_num: Optional[int] = None
    win_entrant_id = ""

    if results:
        winner_meta = next((res for res in results if isinstance(res, dict) and int(res.get("position") or 0) == 1), None)
        if winner_meta is None and isinstance(results[0], dict):
            winner_meta = results[0]
        if winner_meta:
            raw_num = winner_meta.get("runner_number")
            if raw_num is not None and str(raw_num).strip() != "":
                try:
                    win_num = int(raw_num)
                except (TypeError, ValueError):
                    win_num = None
            win_entrant_id = str(winner_meta.get("entrant_id") or "").strip()

    if win_num is None:
        top4 = race.get("official_top4") or []
        if top4:
            first = top4[0]
            if isinstance(first, dict):
                winner_meta = winner_meta or first
                raw_num = first.get("runner_number")
                if raw_num is not None:
                    try:
                        win_num = int(raw_num)
                    except (TypeError, ValueError):
                        win_num = None
            else:
                try:
                    win_num = int(first)
                except (TypeError, ValueError):
                    win_num = None

    win_runner: Optional[Dict[str, Any]] = None
    if win_num is not None:
        win_runner = next((r for r in all_runners if isinstance(r, dict) and int(r.get("runner_number") or 0) == win_num), None)
    if win_runner is None and win_entrant_id:
        win_runner = next((r for r in all_runners if isinstance(r, dict) and str(r.get("entrant_id") or "").strip() == win_entrant_id), None)
    if win_runner is None:
        win_runner = next(
            (r for r in all_runners if isinstance(r, dict) and int(r.get("finishing_position") or r.get("position") or 0) == 1),
            None,
        )

    if winner_meta is None and win_runner is not None:
        winner_meta = {
            "runner_number": win_runner.get("runner_number"),
            "barrier": win_runner.get("barrier"),
            "position": 1,
        }

    return winner_meta, win_runner, field_sz


def compute_meeting_bias(settled_races: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Infer live current-meeting track bias from earlier finalized races at the same venue."""
    if not settled_races:
        return {
            "sample_size": 0,
            "pace_bias": "Neutral",
            "barrier_bias": "Neutral",
            "on_pace_wins": 0,
            "closer_wins": 0,
            "neutral_wins": 0,
            "inside_wins": 0,
            "outside_wins": 0,
            "summary": "No earlier settled races yet today.",
        }

    on_pace_wins = 0
    closer_wins = 0
    neutral_wins = 0
    inside_wins = 0
    outside_wins = 0
    valid_races = 0

    for race in settled_races:
        winner_meta, win_runner, field_sz = _resolve_race_winner(race)
        if winner_meta is None and win_runner is None:
            continue
        valid_races += 1

        win_bar = int((winner_meta or {}).get("barrier") or (win_runner or {}).get("barrier") or 0)
        if win_bar > 0:
            if win_bar <= max(3, field_sz // 3):
                inside_wins += 1
            elif win_bar >= max(6, int(field_sz * 0.65)):
                outside_wins += 1

        style_source = win_runner if win_runner is not None else (winner_meta or {})
        pace_bucket = classify_runner_pace_style(style_source)
        if pace_bucket == "on_pace":
            on_pace_wins += 1
        elif pace_bucket == "closer":
            closer_wins += 1
        else:
            neutral_wins += 1

    if valid_races == 0:
        return {
            "sample_size": 0,
            "pace_bias": "Neutral",
            "barrier_bias": "Neutral",
            "on_pace_wins": 0,
            "closer_wins": 0,
            "neutral_wins": 0,
            "inside_wins": 0,
            "outside_wins": 0,
            "summary": "No earlier settled races yet today.",
        }

    n = valid_races
    if on_pace_wins >= max(2, int(n * 0.6)):
        pace_bias = "On-Pace"
    elif closer_wins >= max(2, int(n * 0.6)):
        pace_bias = "Swoopers"
    else:
        pace_bias = "Neutral"

    if inside_wins >= max(2, int(n * 0.6)):
        barrier_bias = "Inside"
    elif outside_wins >= max(2, int(n * 0.6)):
        barrier_bias = "Outside"
    else:
        barrier_bias = "Neutral"

    summary = f"From {n} race(s): Pace={pace_bias} ({on_pace_wins} on-pace / {closer_wins} closers), Rail/Draw={barrier_bias} ({inside_wins} inside / {outside_wins} wide)."
    return {
        "sample_size": n,
        "pace_bias": pace_bias,
        "barrier_bias": barrier_bias,
        "on_pace_wins": on_pace_wins,
        "closer_wins": closer_wins,
        "neutral_wins": neutral_wins,
        "inside_wins": inside_wins,
        "outside_wins": outside_wins,
        "summary": summary,
    }


def apply_progressive_meeting_bias(
    race: Dict[str, Any],
    earlier_finished_races: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Compute race's progressive intraday `meeting_bias` and update factor #4 (`current_meeting_bias`)
    on every scored runner strictly from earlier finished races (`race_number < current_race_number`).
    Race 1 (or any race with 0 earlier finished races) stays strictly neutral 5.0 / has_data=False.
    Does NOT alter pre-race win_selection / pricing / bets (display & factor attribution only).
    """
    curr_num = int(race.get("race_number") or 0)
    strictly_earlier = [
        r
        for r in earlier_finished_races
        if int(r.get("race_number") or 0) < curr_num
        and (
            bool(r.get("results"))
            or bool(r.get("official_top4"))
            or any(
                int(run.get("finishing_position") or 0) == 1
                for run in (r.get("scored_runners") or r.get("runners") or [])
                if isinstance(run, dict)
            )
        )
    ]
    bias = compute_meeting_bias(strictly_earlier)
    race["meeting_bias"] = bias

    scored_runners = race.get("scored_runners") or []
    field_size = max(len(scored_runners), 1)

    for runner in scored_runners:
        barrier = int(runner.get("barrier") or 0)
        barrier_pct = (barrier / field_size) if barrier > 0 else 0.5
        pace_bucket = classify_runner_pace_style(runner)
        is_leader = _is_leader_runner(runner)
        is_on_pace = pace_bucket == "on_pace"
        is_closer = pace_bucket == "closer"

        if bias["sample_size"] == 0:
            mb_score = NEUTRAL_SCORE
            mb_has_data = False
            mb_reason = "No earlier settled races at this meeting yet."
        else:
            sc = 5.0
            if bias["pace_bias"] == "On-Pace":
                sc += 2.0 if is_on_pace else (-1.4 if is_closer else 0.0)
            elif bias["pace_bias"] == "Swoopers":
                sc += 2.0 if is_closer else (-1.4 if is_leader else 0.0)
            if bias["barrier_bias"] == "Inside" and barrier_pct <= 0.4:
                sc += 1.0
            elif bias["barrier_bias"] == "Outside" and barrier_pct >= 0.6:
                sc += 1.0
            mb_score = clamp(sc)
            mb_has_data = True
            mb_reason = bias["summary"]

        for factor in runner.get("audit_23pt") or []:
            if factor.get("key") == "current_meeting_bias":
                factor["score"] = round(mb_score, 2)
                factor["has_data"] = mb_has_data
                factor["reason"] = mb_reason
                factor["weighted_points"] = round((mb_score / 10.0) * float(factor.get("weight") or 5.0), 3)
                break

    return bias


def _parse_rail_meters(rail_position: str) -> float:
    m = re.search(r"\+?\s*(\d+(?:\.\d+)?)\s*m", (rail_position or "").lower())
    return float(m.group(1)) if m else 0.0


BAYESIAN_SR_PRIOR = 0.12
BAYESIAN_PR_PRIOR = 0.32
BAYESIAN_PRIOR_STARTS = 25.0
PICNIC_PRIZE_THRESHOLD = 15000.0


def bayesian_strike_rate(
    wins: int,
    starts: int,
    prior_sr: float = BAYESIAN_SR_PRIOR,
    k: float = BAYESIAN_PRIOR_STARTS,
) -> float:
    """Apply Empirical Bayes shrinkage toward prior_sr with pseudo-count k."""
    w = max(0.0, float(wins))
    s = max(0.0, float(starts))
    return (w + prior_sr * k) / (s + k)


def _extract_structured_runs(runner: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract structured past-start dicts from `last_twenty_starts` (if list) first,
    then fall back to `past_performances` (if list of dicts).
    """
    raw_l20 = runner.get("last_twenty_starts")
    if isinstance(raw_l20, list):
        runs = [x for x in raw_l20 if isinstance(x, dict)]
        if runs:
            return runs
    raw_pp = runner.get("past_performances")
    if isinstance(raw_pp, list):
        runs = [x for x in raw_pp if isinstance(x, dict)]
        if runs:
            return runs
    return []


def _last_twenty_string(runner: Dict[str, Any]) -> str:
    raw_l20 = runner.get("last_twenty_starts")
    if isinstance(raw_l20, str):
        return raw_l20.strip()
    if isinstance(raw_l20, list):
        chars: List[str] = []
        for entry in raw_l20:
            if isinstance(entry, dict):
                pos = entry.get("position") or entry.get("finishing_position") or entry.get("finish_position")
                try:
                    p = int(pos)
                    if p > 0:
                        chars.append("0" if p >= 10 else str(p))
                except (TypeError, ValueError):
                    pass
            elif isinstance(entry, str) and entry.strip():
                chars.append(entry.strip()[0])
        return "".join(chars)
    return ""


def _parse_run_float(run: Dict[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        val = run.get(k)
        if val is not None and val != "":
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None


def _is_picnic_or_non_tab_run(run: Dict[str, Any]) -> bool:
    if run.get("is_picnic") or run.get("non_tab") or run.get("is_non_tab"):
        return True
    prize = _parse_run_float(run, "prize_money", "purse", "prizemoney", "total_prize_money")
    if prize is not None and 0.0 < prize < PICNIC_PRIZE_THRESHOLD:
        return True
    text_blob = " ".join(
        str(run.get(k) or "") for k in ("venue", "track", "race_name", "race_class", "class", "meeting_type")
    ).lower()
    if "picnic" in text_blob or "non-tab" in text_blob or "non tab" in text_blob:
        return True
    return False


def _score_structured_sectionals(runs: List[Dict[str, Any]]) -> Tuple[float, str]:
    """
    Score Adjusted Sectionals (#6) from structured past-run dicts, heavily penalizing:
      - beaten margins > 10.0L (and severe > 20.0L tail-offs)
      - last-place finishes in small fields (field_size <= 8)
      - non-TAB / picnic races (prize_money < $15,000)
    """
    recent = runs[:4]
    if not recent:
        return NEUTRAL_SCORE, "No prior starts recorded (debutant)."

    # Weight most-recent runs highest (index 0 = most recent if dated desc or default API order)
    dated = [r for r in recent if r.get("date") or r.get("meeting_date") or r.get("start_time")]
    if len(dated) == len(recent):
        recent = sorted(
            recent,
            key=lambda x: str(x.get("date") or x.get("meeting_date") or x.get("start_time") or ""),
            reverse=True,
        )

    weights = [1.5, 1.1, 0.8, 0.6][: len(recent)]
    pts = 0.0
    close_runs = 0
    severe_tail_offs = 0
    heavy_margin_runs = 0
    picnic_runs = 0
    small_field_lasts = 0
    worst_margin = 0.0

    for run, w in zip(recent, weights):
        pos_f = _parse_run_float(run, "position", "finishing_position", "finish_position")
        pos = int(pos_f) if pos_f is not None and pos_f > 0 else 6
        margin = _parse_run_float(run, "margin", "margin_length", "beaten_margin")
        field_sz_f = _parse_run_float(run, "field_size", "starters", "runners_count", "field_count")
        field_sz = int(field_sz_f) if field_sz_f is not None and field_sz_f > 0 else 0
        is_picnic = _is_picnic_or_non_tab_run(run)

        if pos == 1:
            run_sc = 9.5
        elif pos == 2:
            run_sc = 8.0
        elif pos == 3:
            run_sc = 7.0
        elif pos <= 5:
            run_sc = 5.5
        else:
            run_sc = max(2.0, 5.0 - (pos - 5) * 0.5)

        if pos > 1 and margin is not None:
            worst_margin = max(worst_margin, margin)
            if margin > 20.0:
                severe_tail_offs += 1
                run_sc = min(2.2, run_sc - 4.2)
            elif margin > 10.0:
                heavy_margin_runs += 1
                run_sc = min(3.6, run_sc - 2.5)
            elif margin <= 2.0:
                close_runs += 1
                run_sc = min(9.8, run_sc + 0.6)

        if field_sz > 0 and field_sz <= 8 and pos >= field_sz:
            small_field_lasts += 1
            run_sc = max(1.2, run_sc - 1.5)

        if is_picnic:
            picnic_runs += 1
            run_sc = min(4.4, run_sc - 1.2)

        pts += clamp(run_sc) * w

    score = clamp(pts / sum(weights))
    if close_runs >= 2 and severe_tail_offs == 0 and picnic_runs == 0:
        score = clamp(score + 0.7)

    # Strict anti-inflation cap: a >20L tail-off or picnic heavy defeat cannot score above neutral 5.0
    if severe_tail_offs > 0 or (picnic_runs > 0 and (heavy_margin_runs > 0 or small_field_lasts > 0)):
        score = min(score, 4.2)
    elif picnic_runs == len(recent):
        score = min(score, 4.5)

    flags: List[str] = []
    if severe_tail_offs > 0:
        flags.append(f"tail-off beaten {worst_margin:.1f}L")
    elif heavy_margin_runs > 0:
        flags.append(f"beaten >10L ({worst_margin:.1f}L)")
    if small_field_lasts > 0:
        flags.append(f"last in small field ({small_field_lasts}x)")
    if picnic_runs > 0:
        flags.append(f"picnic/non-TAB (<$15k) x{picnic_runs}")
    if close_runs >= 2 and not flags:
        flags.append(f"tight margins in {close_runs} of last {len(recent)} runs")

    suffix = f" ({'; '.join(flags)})." if flags else "."
    return score, f"Structured form ({len(recent)} recent starts) rated {score:.1f}/10{suffix}"


def _score_structured_ctd(runs: List[Dict[str, Any]]) -> Tuple[float, str]:
    """
    Score Class, Track & Distance (#23) from structured runs with discounts for non-TAB/picnic
    purses (< $15,000), beaten margins > 10L / > 20L, and last-place finishes in small fields.
    """
    if not runs:
        return NEUTRAL_SCORE, "Unraced debutant — neutral class/distance baseline."

    eff_wins = 0.0
    eff_places = 0.0
    raw_wins = 0
    raw_places = 0
    starts = 0
    picnic_count = 0
    tail_off_penalty = 0.0
    has_recent_severe_tail_off = False

    for idx, run in enumerate(runs):
        pos_f = _parse_run_float(run, "position", "finishing_position", "finish_position")
        if pos_f is None or pos_f <= 0:
            continue
        pos = int(pos_f)
        starts += 1
        is_picnic = _is_picnic_or_non_tab_run(run)
        if is_picnic:
            picnic_count += 1
        class_weight = 0.25 if is_picnic else 1.0

        if pos == 1:
            raw_wins += 1
            raw_places += 1
            eff_wins += class_weight
            eff_places += class_weight
        elif pos in (2, 3):
            raw_places += 1
            eff_places += class_weight

        margin = _parse_run_float(run, "margin", "margin_length", "beaten_margin")
        field_sz_f = _parse_run_float(run, "field_size", "starters", "runners_count", "field_count")
        field_sz = int(field_sz_f) if field_sz_f is not None and field_sz_f > 0 else 0

        if pos > 1 and margin is not None:
            if margin > 20.0:
                tail_off_penalty += 1.4 if idx < 3 else 0.6
                if idx < 3:
                    has_recent_severe_tail_off = True
            elif margin > 10.0:
                tail_off_penalty += 0.6 if idx < 3 else 0.3
        if field_sz > 0 and field_sz <= 8 and pos >= field_sz:
            tail_off_penalty += 0.7 if idx < 3 else 0.3

    if starts == 0:
        return NEUTRAL_SCORE, "Unraced debutant — neutral class/distance baseline."

    win_rate = eff_wins / starts
    place_rate = eff_places / starts
    score = clamp(4.2 + win_rate * 10.0 + place_rate * 3.5 - min(3.0, tail_off_penalty))
    if has_recent_severe_tail_off or (picnic_count > 0 and picnic_count >= starts / 2.0):
        score = min(score, 4.5)

    notes: List[str] = []
    if picnic_count > 0:
        notes.append(f"{picnic_count} picnic/non-TAB")
    if tail_off_penalty > 0:
        notes.append("margin/small-field penalty")
    extra = f" [{', '.join(notes)}]" if notes else ""
    return score, f"{raw_wins}W-{raw_places - raw_wins}P from {starts} structured starts{extra}."


def _recent_form_score(last_twenty: str) -> Tuple[float, str]:
    """Score finishing string like '06x516x551' (rightmost = most recent)."""
    cleaned = re.sub(r"[^0-9xX]", "", last_twenty or "")
    runs = [ch for ch in cleaned if ch.isdigit()]
    if not runs:
        return NEUTRAL_SCORE, "No prior starts recorded (debutant)."
    recent = runs[-4:]
    pts = 0.0
    weights = [0.6, 0.8, 1.1, 1.5][-len(recent):]
    for ch, w in zip(recent, weights):
        pos = 10 if ch == "0" else int(ch)
        if pos == 1:
            pts += 9.5 * w
        elif pos == 2:
            pts += 8.0 * w
        elif pos == 3:
            pts += 7.0 * w
        elif pos <= 5:
            pts += 5.5 * w
        else:
            pts += max(2.0, 5.0 - (pos - 5) * 0.5) * w
    score = clamp(pts / sum(weights))
    return score, f"Recent starts '{last_twenty[-6:]}' rated {score:.1f}/10."


def _days_since_last_run(past_perfs: List[Dict[str, Any]], race_date_str: str) -> Optional[int]:
    if not past_perfs:
        return None
    try:
        target = datetime.strptime(race_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None
    for pp in past_perfs:
        if not isinstance(pp, dict):
            continue
        raw_dt = pp.get("date") or pp.get("meeting_date") or pp.get("start_time")
        if not raw_dt:
            continue
        try:
            d = datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            delta = (target - d).days
            if delta >= 0:
                return delta
        except Exception:
            continue
    return None


def attach_enrichment_to_runners(
    runners: List[Dict[str, Any]],
    trials_by_horse: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    pre_stewards_by_horse: Optional[Dict[str, Dict[str, List[str]]]] = None,
    blog_notes_by_horse: Optional[Dict[str, List[str]]] = None,
    nsw_freefields_checked: bool = False,
) -> None:
    """Attach zero-key NSW FreeFields trials, Pre-Stewards/vet notes, and Ladbrokes blog notes to runners."""
    trials_by_horse = trials_by_horse or {}
    pre_stewards_by_horse = pre_stewards_by_horse or {}
    blog_notes_by_horse = blog_notes_by_horse or {}

    for r in runners:
        norm = normalize_horse_name(r.get("name") or "")
        if not norm:
            continue
        if nsw_freefields_checked:
            r["nsw_freefields_checked"] = True

        if norm in trials_by_horse:
            existing_t = list(r.get("trial_runs") or [])
            r["trial_runs"] = trials_by_horse[norm][:3] + existing_t

        ps = pre_stewards_by_horse.get(norm)
        if ps:
            r["stewards_notes"] = list(dict.fromkeys((r.get("stewards_notes") or []) + (ps.get("stewards_notes") or [])))[:4]
            r["vet_notes"] = list(dict.fromkeys((r.get("vet_notes") or []) + (ps.get("vet_notes") or [])))[:3]
            r["vet_clean_notes"] = list(dict.fromkeys((r.get("vet_clean_notes") or []) + (ps.get("vet_clean_notes") or [])))[:3]
            if ps.get("trial_notes"):
                r["stewards_trial_notes"] = ps["trial_notes"][:2]

        bn = blog_notes_by_horse.get(norm)
        if bn:
            r["blog_notes"] = bn[:2]


def score_race_23pt(
    race: Dict[str, Any],
    track_meta: Dict[str, Any],
    weather: Dict[str, Any],
    meeting_bias: Optional[Dict[str, Any]] = None,
    youtube_transcript: str = "",
    weights_cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Score every non-scratched runner across all 23 factors deterministically.
    Missing text fields (stewards, trials, injury/vet, commentary, YouTube) strictly get 5.0 / 'No data available'.
    """
    weights_cfg = weights_cfg or load_factor_weights()
    factors_meta = weights_cfg["factors"]
    total_weight = sum(f["weight"] for f in factors_meta) or 100.0

    meeting_bias = meeting_bias or {"sample_size": 0, "pace_bias": "Neutral", "barrier_bias": "Neutral"}
    active_runners = [r for r in (race.get("runners") or []) if not r.get("is_scratched")]
    if not active_runners:
        return []

    field_size = len(active_runners)
    avg_weight = sum(float(r.get("weight_carried") or 57.0) for r in active_runners) / field_size
    rail_m = _parse_rail_meters(race.get("rail_position") or "True")
    track_cond_band = race.get("track_condition_band") or "Good"
    distance = int(race.get("distance") or 1200)
    home_straight = int(race.get("track_home_straight") or track_meta.get("home_straight_m") or 350)
    headwind_kmh = float(weather.get("headwind_kmh") or 0.0)
    rain_mm = float(weather.get("rain_expected_mm") or 0.0)
    race_date = race.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    race_comment = (race.get("race_comment") or "").strip()

    # Count leaders to gauge expected race tempo
    leader_count = sum(
        1 for r in active_runners if any(k in (r.get("speedmap_label") or "").lower() for k in ("lead", "front"))
    )

    scored_runners: List[Dict[str, Any]] = []
    for r in active_runners:
        barrier = int(r.get("barrier") or 0)
        barrier_pct = (barrier / max(field_size, 1)) if barrier > 0 else 0.5
        sm_label = (r.get("speedmap_label") or "").lower()
        pace_bucket = classify_runner_pace_style(r)
        is_leader = _is_leader_runner(r)
        is_on_pace = pace_bucket == "on_pace"
        is_closer = pace_bucket == "closer"
        sire_l = (r.get("sire") or "").lower()
        jockey_l = (r.get("jockey") or "").lower()
        trainer_l = (r.get("trainer") or "").lower()
        gear = (r.get("gear") or "").strip()
        structured_runs = _extract_structured_runs(r)
        last_twenty = _last_twenty_string(r)
        past_perfs = r.get("past_performances") or []
        jockey_perfs = r.get("jockey_past_performances") or []

        fluc = compute_market_fluc(float(r.get("opening_win") or 0.0), float(r.get("live_win") or 0.0))

        audit: Dict[str, Dict[str, Any]] = {}

        # 1. Weather & Ground Forecast
        wet_track = track_cond_band in ("Soft", "Heavy") or rain_mm >= 4.0
        wet_sire_match = any(s in sire_l for s in WET_SIRES)
        if wet_track:
            w_score = 7.2 if wet_sire_match else 5.2
            w_reason = (
                f"{track_cond_band} ground ({rain_mm:.1f}mm forecast) suits wet-track sire {r.get('sire') or 'profile'}."
                if wet_sire_match
                else f"{track_cond_band} surface ({rain_mm:.1f}mm forecast); neutral wet-ground baseline."
            )
        else:
            w_score = 5.8 if track_cond_band == "Good" else 5.4
            w_reason = f"{track_cond_band} conditions ({weather.get('summary') or 'dry'})."
        audit["weather_ground"] = {"score": clamp(w_score), "has_data": True, "reason": w_reason}

        # 2. Rail Placement
        if rail_m >= 5.0:
            if is_on_pace and barrier_pct <= 0.5:
                rail_score = 7.4
                rail_reason = f"Rail out +{rail_m:.0f}m favours handy profile from barrier {barrier}."
            elif is_closer and barrier_pct > 0.65:
                rail_score = 3.8
                rail_reason = f"Rail out +{rail_m:.0f}m makes wide backmarker pattern tougher."
            else:
                rail_score = 5.2
                rail_reason = f"Rail out +{rail_m:.0f}m; moderate inside/on-pace lean."
        else:
            rail_score = 5.6 if barrier_pct <= 0.45 else 5.0
            rail_reason = f"Rail {race.get('rail_position') or 'True'} provides fair surface."
        audit["rail_placement"] = {"score": clamp(rail_score), "has_data": True, "reason": rail_reason}

        # 3. Wind Speed & Direction Impact
        if abs(headwind_kmh) < 6.0:
            wind_score = 5.0
            wind_reason = f"{weather.get('effect', 'Calm')} ({weather.get('wind_speed_kmh', 0)} km/h); negligible pace bias."
        elif headwind_kmh >= 6.0:
            if is_leader:
                wind_score = clamp(5.0 - min(2.2, headwind_kmh / 10.0))
                wind_reason = f"{weather.get('effect')} ({headwind_kmh:.0f} km/h headwind) penalises exposed leaders."
            elif is_closer or not is_on_pace:
                wind_score = clamp(5.0 + min(2.0, headwind_kmh / 10.0))
                wind_reason = f"{weather.get('effect')} ({headwind_kmh:.0f} km/h headwind) aids horses with cover."
            else:
                wind_score = 5.6
                wind_reason = f"Can take cover into {weather.get('effect').lower()}."
        else:
            tw = abs(headwind_kmh)
            if is_on_pace:
                wind_score = clamp(5.0 + min(2.0, tw / 10.0))
                wind_reason = f"{weather.get('effect')} ({tw:.0f} km/h tailwind) helps leaders sustain speed."
            else:
                wind_score = clamp(5.0 - min(1.5, tw / 12.0))
                wind_reason = f"{weather.get('effect')} makes reeling in leaders harder."
        audit["wind_impact"] = {"score": clamp(wind_score), "has_data": weather.get("source") != "Unavailable", "reason": wind_reason}

        # 4. Current Meeting Track Bias
        if meeting_bias.get("sample_size", 0) == 0:
            audit["current_meeting_bias"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": "No earlier settled races at this meeting yet."}
        else:
            mb_score = 5.0
            if meeting_bias.get("pace_bias") == "On-Pace":
                mb_score += 2.0 if is_on_pace else (-1.4 if is_closer else 0.0)
            elif meeting_bias.get("pace_bias") == "Swoopers":
                mb_score += 2.0 if is_closer else (-1.4 if is_leader else 0.0)
            if meeting_bias.get("barrier_bias") == "Inside" and barrier_pct <= 0.4:
                mb_score += 1.0
            elif meeting_bias.get("barrier_bias") == "Outside" and barrier_pct >= 0.6:
                mb_score += 1.0
            audit["current_meeting_bias"] = {"score": clamp(mb_score), "has_data": True, "reason": meeting_bias.get("summary", "")}

        # 5. Historical Race & Track Bias
        if home_straight <= 300:
            hb_score = 7.0 if is_on_pace else (3.8 if is_closer else 5.2)
            hb_reason = f"Tight {home_straight}m straight favours on-pace profiles."
        elif home_straight >= 410:
            hb_score = 6.2 if not is_leader else 5.3
            hb_reason = f"Long {home_straight}m straight gives all runners a fair chance."
        else:
            hb_score = 5.6 if is_on_pace else 5.1
            hb_reason = track_meta.get("historical_bias", "Standard fair Australian circuit.")
        audit["historical_track_bias"] = {"score": clamp(hb_score), "has_data": True, "reason": hb_reason}

        # 6. Adjusted Sectionals (structured last_twenty_starts / past_performances first, then string fallback)
        if structured_runs:
            sec_score, sec_reason = _score_structured_sectionals(structured_runs)
            sec_has_data = True
        else:
            sec_score, sec_reason = _recent_form_score(last_twenty)
            sec_has_data = bool(last_twenty)
        audit["adjusted_sectionals"] = {"score": sec_score, "has_data": sec_has_data, "reason": sec_reason}

        # 7. Recent Trials & Jumpouts (NSW FreeFields 21d trials + blog/stewards trial mentions)
        trials = r.get("trial_runs") or []
        blog_notes = r.get("blog_notes") or []
        blog_trial_hit = next((b for b in blog_notes if "trial" in b.lower() or "jumpout" in b.lower()), None)
        stewards_trial_notes = r.get("stewards_trial_notes") or []
        nsw_checked = bool(r.get("nsw_freefields_checked"))

        if trials:
            t0 = trials[0]
            t_pos = int(t0.get("position") or t0.get("finish_position") or 5)
            t_margin = float(t0.get("margin") or 0.0)
            t_venue = t0.get("venue") or "NSW"
            t_date = t0.get("date") or "recent"
            if t_pos == 1:
                t_score = 8.4
                t_reason = f"Won {t_venue} barrier trial ({t_date}) cleanly ({len(trials)} recent trial(s))."
            elif t_pos == 2:
                t_score = 7.4 if t_margin <= 1.5 else 6.8
                t_reason = f"2nd in {t_venue} barrier trial ({t_date}, {t_margin:.1f}L margin)."
            elif t_pos == 3:
                t_score = 6.4 if t_margin <= 2.5 else 5.8
                t_reason = f"3rd in {t_venue} barrier trial ({t_date}, {t_margin:.1f}L margin)."
            else:
                t_score = 4.9 if t_margin <= 4.0 else 4.3
                t_reason = f"Finished {t_pos}th in {t_venue} barrier trial ({t_date}, {t_margin:.1f}L)."
            audit["recent_trials"] = {"score": clamp(t_score), "has_data": True, "reason": t_reason}
        elif blog_trial_hit:
            audit["recent_trials"] = {
                "score": 6.8,
                "has_data": True,
                "reason": f"Ladbrokes preview trial note: {blog_trial_hit[:120]}",
            }
        elif stewards_trial_notes:
            st_t = stewards_trial_notes[0]
            sc = 5.4 if "has trial" in st_t.lower() else 4.4
            audit["recent_trials"] = {"score": sc, "has_data": True, "reason": st_t[:130]}
        elif nsw_checked:
            audit["recent_trials"] = {
                "score": NEUTRAL_SCORE,
                "has_data": True,
                "reason": "Checked Racing NSW FreeFields (21d): no recent barrier trial (neutral 5.0).",
            }
        else:
            audit["recent_trials"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": NO_DATA_LABEL}

        # 8. Pedigree & Sire/Dam Suitability
        if not sire_l:
            audit["pedigree_suitability"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": NO_DATA_LABEL}
        else:
            ped_score = 5.4
            ped_notes = []
            if distance <= 1200 and any(s in sire_l for s in SPRINT_SIRES):
                ped_score += 2.0
                ped_notes.append("proven sprint sire")
            elif distance >= 1600 and any(s in sire_l for s in STAYING_SIRES):
                ped_score += 2.2
                ped_notes.append("proven staying sire")
            if wet_track and wet_sire_match:
                ped_score += 1.2
                ped_notes.append("wet-track progeny affinity")
            reason = f"{r.get('sire')} x {r.get('dam') or 'Dam'}" + (f" ({', '.join(ped_notes)})." if ped_notes else " — neutral distance/ground fit.")
            audit["pedigree_suitability"] = {"score": clamp(ped_score), "has_data": True, "reason": reason}

        # 9. Jockey Recent Form (Bayesian shrinkage: 12% prior, k=25 pseudo-starts)
        j_stats = r.get("jockey_stats") or {}
        last50 = j_stats.get("last_50_starts") if isinstance(j_stats, dict) else None
        j_score = 5.2
        j_reason = f"Jockey: {r.get('jockey')}"
        if isinstance(last50, dict) and int(last50.get("number_of_starts") or 0) > 0:
            st = int(last50["number_of_starts"])
            w = int(last50.get("number_of_wins") or 0)
            p = w + int(last50.get("number_of_seconds") or 0) + int(last50.get("number_of_thirds") or 0)
            raw_sr = w / st
            bayes_sr = bayesian_strike_rate(w, st)
            bayes_pr = bayesian_strike_rate(p, st, prior_sr=BAYESIAN_PR_PRIOR)
            j_score = clamp(4.2 + bayes_sr * 18.0 + bayes_pr * 3.5 + (0.6 if any(ej in jockey_l for ej in ELITE_JOCKEYS) else 0.0))
            j_reason += f" ({w}W/{p}P from {st} rides, {raw_sr*100:.0f}% raw → {bayes_sr*100:.1f}% Bayes SR)."
        elif any(ej in jockey_l for ej in ELITE_JOCKEYS):
            j_score += 2.4
            j_reason += " (top metro strike-rate rider)."
        elif jockey_perfs:
            st = len(jockey_perfs)
            wins = sum(1 for jp in jockey_perfs if isinstance(jp, dict) and int(jp.get("position") or 99) == 1)
            places = sum(1 for jp in jockey_perfs if isinstance(jp, dict) and int(jp.get("position") or 99) <= 3)
            bayes_sr = bayesian_strike_rate(wins, st)
            bayes_pr = bayesian_strike_rate(places, st, prior_sr=BAYESIAN_PR_PRIOR)
            j_score = clamp(4.5 + bayes_sr * 12.0 + bayes_pr * 2.0)
            j_reason += f" ({wins}W/{places}P in {st} tracked rides, {bayes_sr*100:.1f}% Bayes SR)."
        else:
            j_reason += " (standard riding profile)."
        audit["jockey_form"] = {"score": clamp(j_score), "has_data": bool(r.get("jockey") and r.get("jockey") != "TBA"), "reason": j_reason}

        # 10. Trainer Recent Form (Bayesian shrinkage: 12% prior, k=25 pseudo-starts)
        tr_combo = j_stats.get("trainer") if isinstance(j_stats, dict) else None
        t_score = 5.2
        if isinstance(tr_combo, dict) and int(tr_combo.get("number_of_starts") or 0) >= 5:
            tst = int(tr_combo["number_of_starts"])
            tw = int(tr_combo.get("number_of_wins") or 0)
            raw_tsr = tw / tst
            bayes_tsr = bayesian_strike_rate(tw, tst)
            t_score = clamp(4.5 + bayes_tsr * 16.0 + (1.0 if any(et in trainer_l for et in ELITE_TRAINERS) else 0.0))
            t_reason = f"Stable {r.get('trainer')}: {tw}W/{tst} ({raw_tsr*100:.0f}% raw → {bayes_tsr*100:.1f}% Bayes SR with jockey)."
        elif any(et in trainer_l for et in ELITE_TRAINERS):
            t_score = 7.6
            t_reason = f"Leading stable ({r.get('trainer')})."
        else:
            t_reason = f"Stable: {r.get('trainer')} (standard provincial/metro baseline)."
        audit["trainer_form"] = {"score": clamp(t_score), "has_data": bool(r.get("trainer")), "reason": t_reason}

        # 11. Weight & Apprentice Claims
        carried = float(r.get("weight_carried") or 57.0)
        claim = float(r.get("apprentice_claim_kg") or 0.0)
        w_diff = avg_weight - carried  # positive = lighter than field avg
        wt_score = clamp(5.2 + w_diff * 0.45 + min(1.2, claim * 0.35))
        wt_reason = f"Carries {carried:.1f}kg (field avg {avg_weight:.1f}kg" + (f", -{claim:.1f}kg claim)." if claim > 0 else ").")
        audit["weight_claims"] = {"score": wt_score, "has_data": True, "reason": wt_reason}

        # 12. Jockey Track Familiarity (includes horse+jockey stats when provided)
        pp_stats = r.get("past_performance_stats") or {}
        hj_stat = pp_stats.get("jockey") if isinstance(pp_stats, dict) else None
        venue_name = (race.get("meeting_name") or track_meta.get("name") or "").lower()
        venue_rides = [
            jp for jp in jockey_perfs if isinstance(jp, dict) and venue_name and venue_name in str(jp.get("track") or jp.get("meeting") or "").lower()
        ]
        if isinstance(hj_stat, dict) and int(hj_stat.get("number_of_starts") or 0) > 0:
            hs = int(hj_stat["number_of_starts"])
            hw = int(hj_stat.get("number_of_wins") or 0)
            jf_score = clamp(5.6 + (hw / hs) * 3.2 + min(1.0, hs * 0.25))
            jf_reason = f"Jockey has {hw}W from {hs} ride(s) on {r.get('name')}."
        elif venue_rides:
            vw = sum(1 for jp in venue_rides if int(jp.get("position") or 99) == 1)
            jf_score = clamp(5.5 + vw * 1.5 + min(1.5, len(venue_rides) * 0.3))
            jf_reason = f"{len(venue_rides)} recent ride(s) at {race.get('meeting_name')} ({vw} win(s))."
        elif any(ej in jockey_l for ej in ELITE_JOCKEYS):
            jf_score = 6.5
            jf_reason = f"Experienced metro jockey ({r.get('jockey')}) with strong venue familiarity."
        else:
            jf_score = NEUTRAL_SCORE
            jf_reason = "Standard track familiarity."
        audit["jockey_track_familiarity"] = {"score": jf_score, "has_data": bool(hj_stat or venue_rides or any(ej in jockey_l for ej in ELITE_JOCKEYS)), "reason": jf_reason}

        # 13. Speedmap & Pace Position
        if not sm_label:
            sm_score = NEUTRAL_SCORE
            sm_reason = "No speedmap settling position available."
        else:
            if leader_count <= 1 and is_leader:
                sm_score = 8.2
                sm_reason = f"Sole/soft leader ({r.get('speedmap_label')}); likely to control tempo."
            elif leader_count >= 3 and is_leader:
                sm_score = 4.6
                sm_reason = f"High early speed pressure ({leader_count} leaders); risk of over-racing."
            elif leader_count >= 3 and (is_closer or not is_on_pace):
                sm_score = 7.4
                sm_reason = f"Hot tempo ({leader_count} leaders) sets race up for {r.get('speedmap_label')}."
            elif is_on_pace:
                sm_score = 6.8
                sm_reason = f"Ideal handy map ({r.get('speedmap_label')}, {r.get('settling_lengths')}L)."
            else:
                sm_score = 5.4
                sm_reason = f"Maps {r.get('speedmap_label')}."
        audit["speedmap_pace"] = {"score": clamp(sm_score), "has_data": bool(sm_label), "reason": sm_reason}

        # 14. Days Since Last Run / Trial
        days_since = _days_since_last_run(past_perfs, race_date)
        if days_since is None:
            # Infer first-up from 'x' at end or second-last of last_twenty
            if last_twenty.lower().endswith("x"):
                ds_score, ds_reason = 5.8, "Resuming fresh from spell."
            elif len(last_twenty) >= 2 and last_twenty[-2].lower() == "x":
                ds_score, ds_reason = 6.8, "Second-up fitness profile."
            else:
                ds_score, ds_reason = NEUTRAL_SCORE, "Days since last run unavailable."
            ds_has = bool(last_twenty)
        else:
            ds_has = True
            if 10 <= days_since <= 24:
                ds_score, ds_reason = 7.2, f"Peak spacing ({days_since} days since last run)."
            elif 6 <= days_since < 10:
                ds_score, ds_reason = 6.2, f"Quick {days_since}-day backup."
            elif 25 <= days_since <= 42:
                ds_score, ds_reason = 5.8, f"Fresh {days_since}-day spacing."
            elif days_since > 90:
                ds_score, ds_reason = 5.4, f"Resuming off {days_since}-day spell."
            else:
                ds_score, ds_reason = 5.0, f"{days_since} days since last start."
        audit["days_since_run"] = {"score": clamp(ds_score), "has_data": ds_has, "reason": ds_reason}

        # 15. Gear Changes
        if not gear:
            audit["gear_changes"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": "No gear listed."}
        else:
            gl = gear.lower()
            if "first time" in gl or "1st" in gl:
                g_score = 7.0 if "blinker" in gl else 6.2
                g_reason = f"Key gear addition: {gear}."
            else:
                g_score = 5.2
                g_reason = f"Standard gear: {gear}."
            audit["gear_changes"] = {"score": clamp(g_score), "has_data": True, "reason": g_reason}

        # 16. Injury & Vet History (NSW Pre-Stewards vet flags/clearances + strict neutral fallback)
        vet_notes = r.get("vet_notes") or []
        vet_clean_notes = r.get("vet_clean_notes") or []
        if vet_notes:
            audit["injury_history"] = {
                "score": 3.4,
                "has_data": True,
                "reason": f"Vet/stand-down flag: {vet_notes[0][:125]}",
            }
        elif vet_clean_notes:
            audit["injury_history"] = {
                "score": 5.3,
                "has_data": True,
                "reason": f"Vet check cleared: {vet_clean_notes[0][:125]}",
            }
        elif nsw_checked:
            audit["injury_history"] = {
                "score": NEUTRAL_SCORE,
                "has_data": True,
                "reason": "Checked Racing NSW Pre-Stewards: clean vet/injury record (neutral 5.0).",
            }
        else:
            audit["injury_history"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": NO_DATA_LABEL}

        # 17. Stewards Comments (NSW Pre-Stewards excuses/adverse notes + strict neutral fallback)
        stewards_notes = r.get("stewards_notes") or []
        if stewards_notes:
            st_text = " ".join(stewards_notes).lower()
            excuse_kws = (
                "held up", "blocked", "checked", "wide without cover", "raced wide",
                "slow to begin", "bumped", "interference", "restricted room", "shifted out on jumping",
            )
            adverse_kws = ("weakened", "disappointing", "overraced", "raced keenly", "failed to respond", "hung out")
            has_excuse = any(kw in st_text for kw in excuse_kws)
            has_adverse = any(kw in st_text for kw in adverse_kws)
            if has_excuse and not has_adverse:
                st_score = 7.1
                st_prefix = "Stewards forgive/excuse: "
            elif has_adverse and not has_excuse:
                st_score = 4.3
                st_prefix = "Stewards caution: "
            else:
                st_score = 5.4
                st_prefix = "Stewards note: "
            audit["stewards_comments"] = {
                "score": st_score,
                "has_data": True,
                "reason": f"{st_prefix}{stewards_notes[0][:125]}",
            }
        elif nsw_checked:
            audit["stewards_comments"] = {
                "score": NEUTRAL_SCORE,
                "has_data": True,
                "reason": "Checked Racing NSW Pre-Stewards: clean stewards record (neutral 5.0).",
            }
        else:
            audit["stewards_comments"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": NO_DATA_LABEL}

        # 18. Trainer & Jockey Commentary (Ladbrokes daily tips blog + race preview + form indicators)
        form_comment = (r.get("form_comment") or "").strip()
        runner_in_race_comment = bool(race_comment and r.get("name") and r["name"].lower() in race_comment.lower())
        form_indicators = r.get("form_indicators") or []
        pos_indicators = [fi["name"] for fi in form_indicators if not fi.get("negative")]
        if blog_notes:
            audit["trainer_jockey_commentary"] = {
                "score": 6.6,
                "has_data": True,
                "reason": f"Ladbrokes Daily Tips: {blog_notes[0][:130]}",
            }
        elif form_comment:
            audit["trainer_jockey_commentary"] = {"score": 6.1, "has_data": True, "reason": form_comment[:130]}
        elif runner_in_race_comment:
            audit["trainer_jockey_commentary"] = {
                "score": 6.0,
                "has_data": True,
                "reason": f"Highlighted in official race preview for {r.get('name')}.",
            }
        elif pos_indicators:
            audit["trainer_jockey_commentary"] = {
                "score": 5.8,
                "has_data": True,
                "reason": f"Form indicator: {', '.join(pos_indicators[:2])}.",
            }
        else:
            audit["trainer_jockey_commentary"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": NO_DATA_LABEL}

        # 19. Target-Race Preparation Weighting
        prize = float(r.get("prize_money") or 0.0)
        race_prize = float(race.get("prize_money") or 50000.0)
        if prize > 0 and race_prize > 0:
            ratio = prize / max(race_prize, 10000.0)
            prep_score = clamp(5.0 + min(2.5, math.log1p(ratio) * 1.1))
            prep_reason = f"Career earnings ${prize:,.0f} targeting ${race_prize:,.0f} event."
        else:
            prep_score = NEUTRAL_SCORE
            prep_reason = "Standard preparation weighting."
        audit["target_race_prep"] = {"score": prep_score, "has_data": prize > 0, "reason": prep_reason}

        # 20. YouTube Preview Transcripts (Atom XML <media:description> + best-effort captions; STRICT neutral fallback if absent)
        if youtube_transcript and r.get("name") and r["name"].lower() in youtube_transcript.lower():
            audit["youtube_transcripts"] = {
                "score": 6.4,
                "has_data": True,
                "reason": "Mentioned in YouTube preview description/transcript (ready for Gemini sentiment).",
            }
        elif youtube_transcript:
            audit["youtube_transcripts"] = {
                "score": NEUTRAL_SCORE,
                "has_data": True,
                "reason": "YouTube XML preview checked for meeting; runner not singled out (neutral).",
            }
        else:
            audit["youtube_transcripts"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": NO_DATA_LABEL}

        # 21. Live Market Firming / Drifting %
        if fluc["opening_win"] > 1.0 and fluc["live_win"] > 1.0:
            # Map relative market move into score: +20% firmer -> ~8.0, -20% drifter -> ~3.0
            mf_score = clamp(5.5 + (fluc["fluc_pct"] / 8.0))
            mf_reason = (
                f"{fluc['direction']} ({fluc['fluc_pct']:+.1f}% rel, {fluc['implied_prob_delta_pct']:+.1f}pp implied): "
                f"${fluc['opening_win']:.2f} → ${fluc['live_win']:.2f}."
            )
            audit["market_fluc"] = {"score": mf_score, "has_data": True, "reason": mf_reason}
        else:
            audit["market_fluc"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": "Opening/live odds not yet available."}

        # 22. Barrier Draw & Track Geometry
        if barrier <= 0:
            bar_score, bar_reason = NEUTRAL_SCORE, "Barrier not assigned."
        elif barrier_pct <= 0.35:
            bar_score = 7.2 if distance <= 1400 else 6.4
            bar_reason = f"Inside draw (Barrier {barrier}/{field_size}) saves ground around {track_meta.get('turning', 'circuit')} turn."
        elif barrier_pct <= 0.7:
            bar_score = 5.8
            bar_reason = f"Mid-draw (Barrier {barrier}/{field_size}) offers tactical options."
        else:
            bar_score = 3.8 if (distance <= 1300 and home_straight <= 340) else 4.5
            bar_reason = f"Wide gate (Barrier {barrier}/{field_size}) risks covering extra ground."
        audit["barrier_geometry"] = {"score": clamp(bar_score), "has_data": barrier > 0, "reason": bar_reason}

        # 23. Class, Track & Distance Record (structured starts first, then string fallback)
        if structured_runs:
            ctd_score, ctd_reason = _score_structured_ctd(structured_runs)
            ctd_has_data = True
        else:
            wins_total = last_twenty.count("1")
            places_total = wins_total + last_twenty.count("2") + last_twenty.count("3")
            starts_total = sum(1 for c in last_twenty if c.isdigit())
            if starts_total > 0:
                win_rate = wins_total / starts_total
                place_rate = places_total / starts_total
                ctd_score = clamp(4.2 + win_rate * 10.0 + place_rate * 3.5)
                ctd_reason = f"{wins_total}W-{places_total - wins_total}P from last {starts_total} tracked starts (Win {win_rate*100:.0f}%)."
            else:
                ctd_score = NEUTRAL_SCORE
                ctd_reason = "Unraced debutant — neutral class/distance baseline."
            ctd_has_data = starts_total > 0
        audit["class_track_distance"] = {"score": ctd_score, "has_data": ctd_has_data, "reason": ctd_reason}

        # Assemble ordered 23-point audit list & composite score
        audit_list = []
        composite = 0.0
        for fm in factors_meta:
            key = fm["key"]
            w = float(fm["weight"])
            entry = audit[key]
            sc = float(entry["score"])
            weighted_pts = round((sc / 10.0) * w, 3)
            composite += weighted_pts
            audit_list.append(
                {
                    "key": key,
                    "label": fm["label"],
                    "category": fm["category"],
                    "weight": w,
                    "score": round(sc, 2),
                    "weighted_points": weighted_pts,
                    "has_data": bool(entry["has_data"]),
                    "reason": entry["reason"],
                }
            )

        composite_100 = round((composite / total_weight) * 100.0, 2)

        enriched = dict(r)
        enriched["last_twenty_starts"] = last_twenty
        enriched["market_fluc"] = fluc
        enriched["audit_23pt"] = audit_list
        enriched["composite_score"] = composite_100
        # Drop heavyweight raw arrays before persisting to Firestore (keeps docs lean on Spark tier!)
        enriched.pop("past_performances", None)
        enriched.pop("jockey_past_performances", None)
        scored_runners.append(enriched)

    scored_runners.sort(key=lambda x: x["composite_score"], reverse=True)
    return scored_runners
