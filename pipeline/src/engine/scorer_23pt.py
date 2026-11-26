from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def compute_meeting_bias(settled_races: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Infer live current-meeting track bias from earlier finalized races at the same venue."""
    if not settled_races:
        return {
            "sample_size": 0,
            "pace_bias": "Neutral",
            "barrier_bias": "Neutral",
            "summary": "No earlier settled races yet today.",
        }

    on_pace_wins = 0
    closer_wins = 0
    inside_wins = 0
    outside_wins = 0

    for race in settled_races:
        results = race.get("results") or []
        if not results:
            continue
        winner = results[0]
        win_num = winner.get("runner_number")
        win_bar = int(winner.get("barrier") or 0)
        runners = [r for r in (race.get("runners") or []) if not r.get("is_scratched")]
        field_sz = max(len(runners), 1)
        if win_bar > 0:
            if win_bar <= max(3, field_sz // 3):
                inside_wins += 1
            elif win_bar >= max(6, int(field_sz * 0.65)):
                outside_wins += 1
        win_runner = next((r for r in runners if r.get("runner_number") == win_num), None)
        if win_runner:
            sm = (win_runner.get("speedmap_label") or "").lower()
            if any(k in sm for k in ("lead", "pace", "handy", "front")):
                on_pace_wins += 1
            elif any(k in sm for k in ("back", "rear", "tail")):
                closer_wins += 1

    n = len(settled_races)
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
        if int(r.get("race_number") or 0) < curr_num and (r.get("results") or [])
    ]
    bias = compute_meeting_bias(strictly_earlier)
    race["meeting_bias"] = bias

    scored_runners = race.get("scored_runners") or []
    field_size = max(len(scored_runners), 1)

    for runner in scored_runners:
        barrier = int(runner.get("barrier") or 0)
        barrier_pct = (barrier / field_size) if barrier > 0 else 0.5
        sm_label = (runner.get("speedmap_label") or "").lower()
        is_leader = any(k in sm_label for k in ("lead", "front"))
        is_on_pace = is_leader or any(k in sm_label for k in ("pace", "handy", "mid-front"))
        is_closer = any(k in sm_label for k in ("back", "rear", "tail"))

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
        is_leader = any(k in sm_label for k in ("lead", "front"))
        is_on_pace = is_leader or any(k in sm_label for k in ("pace", "handy", "mid-front"))
        is_closer = any(k in sm_label for k in ("back", "rear", "tail"))
        sire_l = (r.get("sire") or "").lower()
        jockey_l = (r.get("jockey") or "").lower()
        trainer_l = (r.get("trainer") or "").lower()
        gear = (r.get("gear") or "").strip()
        last_twenty = (r.get("last_twenty_starts") or "").strip()
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

        # 6. Adjusted Sectionals (derived from past run margins, finish pattern & recent form)
        sec_score, sec_reason = _recent_form_score(last_twenty)
        # Boost slightly if recent past performances have close margins (<2L)
        close_runs = 0
        for pp in past_perfs[:3]:
            if isinstance(pp, dict):
                mg = pp.get("margin") or pp.get("margin_length")
                try:
                    if mg is not None and float(mg) <= 2.0:
                        close_runs += 1
                except (ValueError, TypeError):
                    pass
        if close_runs >= 2:
            sec_score = clamp(sec_score + 0.9)
            sec_reason += f" Tight finishing margins in {close_runs} of last 3 runs."
        audit["adjusted_sectionals"] = {"score": sec_score, "has_data": bool(last_twenty or past_perfs), "reason": sec_reason}

        # 7. Recent Trials & Jumpouts (STRICT neutral fallback if absent)
        trials = r.get("trial_runs") or []
        if not trials:
            audit["recent_trials"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": NO_DATA_LABEL}
        else:
            t0 = trials[0]
            t_pos = int(t0.get("position") or t0.get("finish_position") or 5)
            t_score = 8.2 if t_pos == 1 else (7.0 if t_pos == 2 else (6.0 if t_pos == 3 else 4.8))
            audit["recent_trials"] = {
                "score": clamp(t_score),
                "has_data": True,
                "reason": f"Recent trial/jumpout finished {t_pos} ({len(trials)} trial(s) recorded).",
            }

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

        # 9. Jockey Recent Form (supports Ladbrokes jockey_stats.last_50_starts + elite lookup)
        j_stats = r.get("jockey_stats") or {}
        last50 = j_stats.get("last_50_starts") if isinstance(j_stats, dict) else None
        j_score = 5.2
        j_reason = f"Jockey: {r.get('jockey')}"
        if isinstance(last50, dict) and int(last50.get("number_of_starts") or 0) > 0:
            st = int(last50["number_of_starts"])
            w = int(last50.get("number_of_wins") or 0)
            p = w + int(last50.get("number_of_seconds") or 0) + int(last50.get("number_of_thirds") or 0)
            sr = w / st
            pr = p / st
            j_score = clamp(4.2 + sr * 18.0 + pr * 3.5 + (0.6 if any(ej in jockey_l for ej in ELITE_JOCKEYS) else 0.0))
            j_reason += f" ({w}W/{p}P from last {st} rides, {sr*100:.0f}% SR)."
        elif any(ej in jockey_l for ej in ELITE_JOCKEYS):
            j_score += 2.4
            j_reason += " (top metro strike-rate rider)."
        elif jockey_perfs:
            wins = sum(1 for jp in jockey_perfs if isinstance(jp, dict) and int(jp.get("position") or 99) == 1)
            places = sum(1 for jp in jockey_perfs if isinstance(jp, dict) and int(jp.get("position") or 99) <= 3)
            sr = wins / max(len(jockey_perfs), 1)
            j_score = clamp(4.5 + sr * 12.0 + (places / max(len(jockey_perfs), 1)) * 2.0)
            j_reason += f" ({wins}W/{places}P in last {len(jockey_perfs)} tracked rides)."
        else:
            j_reason += " (standard riding profile)."
        audit["jockey_form"] = {"score": clamp(j_score), "has_data": bool(r.get("jockey") and r.get("jockey") != "TBA"), "reason": j_reason}

        # 10. Trainer Recent Form (supports Ladbrokes jockey_stats.trainer combo + elite lookup)
        tr_combo = j_stats.get("trainer") if isinstance(j_stats, dict) else None
        t_score = 5.2
        if isinstance(tr_combo, dict) and int(tr_combo.get("number_of_starts") or 0) >= 5:
            tst = int(tr_combo["number_of_starts"])
            tw = int(tr_combo.get("number_of_wins") or 0)
            tsr = tw / tst
            t_score = clamp(4.5 + tsr * 16.0 + (1.0 if any(et in trainer_l for et in ELITE_TRAINERS) else 0.0))
            t_reason = f"Stable {r.get('trainer')}: {tw}W/{tst} ({tsr*100:.0f}% SR with jockey)."
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

        # 16. Injury & Vet History (STRICT neutral fallback if absent)
        vet_notes = r.get("vet_notes") or []
        if not vet_notes:
            audit["injury_history"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": NO_DATA_LABEL}
        else:
            audit["injury_history"] = {
                "score": 3.5,
                "has_data": True,
                "reason": f"Vet/injury mention flagged: {vet_notes[0][:110]}",
            }

        # 17. Stewards Comments (STRICT neutral fallback if absent)
        stewards_notes = r.get("stewards_notes") or []
        if not stewards_notes:
            audit["stewards_comments"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": NO_DATA_LABEL}
        else:
            st_text = " ".join(stewards_notes).lower()
            excuse_kws = ("held up", "blocked", "checked", "wide", "slow to begin", "bumped", "interference")
            if any(kw in st_text for kw in excuse_kws):
                audit["stewards_comments"] = {
                    "score": 6.8,
                    "has_data": True,
                    "reason": f"Forgive/excuse noted in stewards text: {stewards_notes[0][:110]}",
                }
            else:
                audit["stewards_comments"] = {
                    "score": 5.2,
                    "has_data": True,
                    "reason": stewards_notes[0][:110],
                }

        # 18. Trainer & Jockey Commentary (STRICT neutral fallback if absent)
        form_comment = (r.get("form_comment") or "").strip()
        runner_in_race_comment = bool(race_comment and r.get("name") and r["name"].lower() in race_comment.lower())
        if not form_comment and not runner_in_race_comment:
            audit["trainer_jockey_commentary"] = {"score": NEUTRAL_SCORE, "has_data": False, "reason": NO_DATA_LABEL}
        else:
            snippet = form_comment if form_comment else f"Highlighted in official race preview for {r.get('name')}."
            audit["trainer_jockey_commentary"] = {"score": 6.0, "has_data": True, "reason": snippet[:120]}

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

        # 20. YouTube Preview Transcripts (STRICT neutral fallback if absent)
        if youtube_transcript and r.get("name") and r["name"].lower() in youtube_transcript.lower():
            audit["youtube_transcripts"] = {
                "score": 6.4,
                "has_data": True,
                "reason": f"Mentioned in configured YouTube preview transcript (pending/ready for Gemini sentiment).",
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

        # 23. Class, Track & Distance Record
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
        audit["class_track_distance"] = {"score": ctd_score, "has_data": starts_total > 0, "reason": ctd_reason}

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
        enriched["market_fluc"] = fluc
        enriched["audit_23pt"] = audit_list
        enriched["composite_score"] = composite_100
        # Drop heavyweight raw arrays before persisting to Firestore (keeps docs lean on Spark tier!)
        enriched.pop("past_performances", None)
        enriched.pop("jockey_past_performances", None)
        scored_runners.append(enriched)

    scored_runners.sort(key=lambda x: x["composite_score"], reverse=True)
    return scored_runners
