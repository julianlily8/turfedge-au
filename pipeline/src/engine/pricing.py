from __future__ import annotations

import itertools
import math
from typing import Any, Dict, List, Optional

from pipeline.src.engine.scorer_23pt import load_factor_weights

SRM_CORRELATION_HAIRCUT = 0.75
MIN_SRM_ODDS = 1.80
UNIT_STAKE = 10.0
SHORT_FAV_ODDS_THRESHOLD = 2.30
TAB_EXOTIC_POOL_FACTOR = 0.79
MIN_EXOTIC_PROJECTED_RETURN = 12.50

# Two-tier market ceiling for $31+ outsiders
OUTSIDER_ODDS_THRESHOLD = 31.0
OUTSIDER_TIER1_MAX_MULT = 2.5
OUTSIDER_TIER2_MAX_MULT = 8.5
OUTSIDER_TIER1_FLUC_CUTOFF = 25.0
OUTSIDER_TIER2_MIN_FLUC_PCT = 30.0
OUTSIDER_MIN_COMPOSITE = 55.0
OUTSIDER_TIER2_MIN_RATED_PRICE = 10.00

MIN_EXOTIC_RUNNERS = {
    "exacta": 6,
    "trifecta": 7,
    "first4": 8,
}


def _softmax_probabilities(scores: List[float], temperature: float = 0.95) -> List[float]:
    if not scores:
        return []
    mean_s = sum(scores) / len(scores)
    std_s = math.sqrt(sum((s - mean_s) ** 2 for s in scores) / max(len(scores), 1))
    scale = max(std_s, 4.0) * max(temperature, 0.25)
    exps = [math.exp((s - mean_s) / scale) for s in scores]
    total = sum(exps) or 1.0
    return [e / total for e in exps]


def _harville_top_k_probs(win_probs: List[float]) -> List[Dict[str, float]]:
    """
    Compute Plackett-Luce / Harville Top 2, Top 3, Top 4, and Top 10 probabilities for each runner.
    Uses exact Harville for Top 2 and fast exact/approximate Harville for Top 3, Top 4, Top 10.
    """
    n = len(win_probs)
    if n == 0:
        return []

    out: List[Dict[str, float]] = []
    for i, pi in enumerate(win_probs):
        # Exact P(2nd) under Harville
        p_2nd = sum(
            pj * (pi / max(1.0 - pj, 1e-9))
            for j, pj in enumerate(win_probs)
            if j != i
        )
        p_top2 = min(0.995, pi + p_2nd)

        # Exact P(3rd) for fields <= 18, otherwise fast Stern/Harville approximation
        if n <= 2:
            p_top3 = 1.0
            p_top4 = 1.0
        else:
            p_3rd = 0.0
            for j, pj in enumerate(win_probs):
                if j == i:
                    continue
                rem1 = max(1.0 - pj, 1e-9)
                for k, pk in enumerate(win_probs):
                    if k == i or k == j:
                        continue
                    rem2 = max(1.0 - pj - pk, 1e-9)
                    p_3rd += pj * (pk / rem1) * (pi / rem2)
            p_top3 = min(0.995, max(p_top2, p_top2 + p_3rd))

            # Smooth monotonic Harville extension for Top 4 and Top 10
            if n <= 3:
                p_top4 = 1.0
            elif n == 4:
                p_top4 = 1.0
            else:
                # Calibrated complement power law anchored to p_top3
                ratio_3_to_2 = (1.0 - p_top3) / max(1.0 - p_top2, 1e-6)
                ratio_3_to_2 = max(0.35, min(0.96, ratio_3_to_2))
                p_top4 = min(0.995, max(p_top3, 1.0 - (1.0 - p_top3) * ratio_3_to_2))

        if n <= 10:
            p_top10 = 1.0
        else:
            # Smooth extension from Top 4 to Top 10
            rem4 = max(1.0 - p_top4, 1e-6)
            p_top10 = min(0.999, max(p_top4, 1.0 - rem4 * ((n - 10) / max(n - 4, 1)) ** 1.15))

        out.append(
            {
                "win": round(pi, 4),
                "top2": round(p_top2, 4),
                "top3": round(p_top3, 4),
                "top4": round(p_top4, 4),
                "top10": round(p_top10, 4),
            }
        )
    return out


def price_and_generate_bets(race: Dict[str, Any], weights_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Convert 23-point scores into calibrated Win / Top 2 / Top 3 / Top 4 / Top 10 probabilities,
    Model Rated Prices, Value Overlay flags, #1 Win Selection, SRM combos (0.75x haircut),
    and suggested Exacta / Trifecta / First 4 combinations.
    """
    weights_cfg = weights_cfg or load_factor_weights()
    temp = float(weights_cfg.get("softmax_temperature", 0.95))
    haircut = float(weights_cfg.get("srm_correlation_haircut", SRM_CORRELATION_HAIRCUT))

    runners = race.get("scored_runners") or []
    if not runners:
        return {
            "win_selection": None,
            "top_10": [],
            "srm_combos": [],
            "exotics": {"exacta": None, "trifecta": None, "first4": None},
        }

    # Blend 70% 23-point model softmax with 30% normalized market implied probability (when odds exist)
    # so rated prices stay calibrated to Australian market liquidity while still exposing genuine overlays.
    model_probs = _softmax_probabilities([float(r["composite_score"]) for r in runners], temperature=temp)
    raw_mkt = [1.0 / float(r["live_win"]) if float(r.get("live_win") or 0) > 1.0 else 0.0 for r in runners]
    mkt_sum = sum(raw_mkt)

    if mkt_sum > 0.5:
        mkt_probs = [p / mkt_sum for p in raw_mkt]
        blended = [0.72 * mp + 0.28 * kp for mp, kp in zip(model_probs, mkt_probs)]

        # Two-tier market ceiling for $31+ outsiders:
        # - Tier 1 (fluc < +25% OR composite < 55.0): cap at 2.5x market-implied probability
        # - Tier 2 (fluc >= +30% AND composite >= 55.0): allow up to 8.5x market-implied prob with $10.00 Rated Price floor
        # - Transition (+25% <= fluc < +30% AND composite >= 55.0): cap at 4.0x market-implied probability
        caps: List[Optional[float]] = []
        for r, kp in zip(runners, mkt_probs):
            lv = float(r.get("live_win") or 0.0)
            fluc_pct = float((r.get("market_fluc") or {}).get("fluc_pct") or 0.0)
            comp = float(r.get("composite_score") or 0.0)
            if lv >= OUTSIDER_ODDS_THRESHOLD and kp > 0.0:
                if fluc_pct >= OUTSIDER_TIER2_MIN_FLUC_PCT and comp >= OUTSIDER_MIN_COMPOSITE:
                    caps.append(min(OUTSIDER_TIER2_MAX_MULT * kp, 1.0 / OUTSIDER_TIER2_MIN_RATED_PRICE))
                elif fluc_pct < OUTSIDER_TIER1_FLUC_CUTOFF or comp < OUTSIDER_MIN_COMPOSITE:
                    caps.append(OUTSIDER_TIER1_MAX_MULT * kp)
                else:
                    caps.append(min(4.0 * kp, 1.0 / OUTSIDER_TIER2_MIN_RATED_PRICE))
            else:
                caps.append(None)

        # Iterative water-filling normalization so capped $31+ runners never exceed their cap
        # after renormalization while total win probabilities still sum cleanly to 1.0.
        win_probs = list(blended)
        locked = [False] * len(win_probs)
        for _ in range(4):
            locked_mass = sum(win_probs[i] for i in range(len(win_probs)) if locked[i])
            free_mass = sum(win_probs[i] for i in range(len(win_probs)) if not locked[i])
            remaining_mass = max(1e-6, 1.0 - locked_mass)
            if free_mass <= 0:
                break
            scale = remaining_mass / free_mass
            any_newly_locked = False
            for i in range(len(win_probs)):
                if not locked[i]:
                    scaled_p = win_probs[i] * scale
                    if caps[i] is not None and scaled_p > caps[i] + 1e-9:
                        win_probs[i] = caps[i]
                        locked[i] = True
                        any_newly_locked = True
                    else:
                        win_probs[i] = scaled_p
            if not any_newly_locked:
                break
    else:
        b_sum = sum(model_probs) or 1.0
        win_probs = [p / b_sum for p in model_probs]

    harville = _harville_top_k_probs(win_probs)

    for idx, (r, h) in enumerate(zip(runners, harville)):
        rated_price = round(min(151.0, max(1.05, 1.0 / max(h["win"], 1e-4))), 2)
        lv_check = float(r.get("live_win") or 0.0)
        fluc_check = float((r.get("market_fluc") or {}).get("fluc_pct") or 0.0)
        comp_check = float(r.get("composite_score") or 0.0)
        if (
            lv_check >= OUTSIDER_ODDS_THRESHOLD
            and fluc_check >= OUTSIDER_TIER2_MIN_FLUC_PCT
            and comp_check >= OUTSIDER_MIN_COMPOSITE
        ):
            rated_price = max(OUTSIDER_TIER2_MIN_RATED_PRICE, rated_price)
        live_win = round(float(r.get("live_win") or 0.0), 2)
        opening_win = round(float(r.get("opening_win") or 0.0), 2)
        # Strict Value Overlay condition: triggered ONLY when Live Price > Model Rated Price
        is_overlay = bool(live_win > 1.0 and live_win > rated_price)
        edge_pct = round(((live_win / rated_price) - 1.0) * 100.0, 1) if (live_win > 1.0 and rated_price > 0) else 0.0

        r["rank"] = idx + 1
        r["rated_price"] = rated_price
        r["is_value_overlay"] = is_overlay
        r["edge_pct"] = edge_pct
        r["probabilities"] = h
        r["opening_win"] = opening_win
        r["live_win"] = live_win

    # Re-sort strictly by model win probability descending so #1 Win Selection is highest rated
    runners.sort(key=lambda x: x["probabilities"]["win"], reverse=True)
    for idx, r in enumerate(runners):
        r["rank"] = idx + 1

    top1 = runners[0]
    win_selection = {
        "runner_number": top1["runner_number"],
        "name": top1["name"],
        "barrier": top1["barrier"],
        "jockey": top1["jockey"],
        "trainer": top1["trainer"],
        "silk_url": top1.get("silk_url") or "",
        "composite_score": top1["composite_score"],
        "win_prob_pct": round(top1["probabilities"]["win"] * 100.0, 1),
        "rated_price": top1["rated_price"],
        "opening_price": top1["opening_win"],
        "live_price": top1["live_win"],
        "is_value_overlay": top1["is_value_overlay"],
        "edge_pct": top1["edge_pct"],
        "market_fluc": top1["market_fluc"],
        "ai_note": top1.get("ai_note") or "",
    }

    top_10 = [
        {
            "rank": r["rank"],
            "runner_number": r["runner_number"],
            "name": r["name"],
            "barrier": r["barrier"],
            "jockey": r["jockey"],
            "composite_score": r["composite_score"],
            "rated_price": r["rated_price"],
            "live_price": r["live_win"],
            "is_value_overlay": r["is_value_overlay"],
            "fluc_direction": r["market_fluc"]["direction"],
            "fluc_pct": r["market_fluc"]["fluc_pct"],
            "probabilities": r["probabilities"],
        }
        for r in runners[:10]
    ]

    # Suggested Same Race Multi combinations (2-leg, 3-leg, 4-leg) with 0.75x correlation haircut
    srm_combos = _build_srm_combos(runners, haircut)

    # Suggested Exacta, Trifecta, First 4 combinations sized to $10 unit flexi stake
    exotics = _build_exotics(runners)

    return {
        "win_selection": win_selection,
        "top_10": top_10,
        "srm_combos": srm_combos,
        "exotics": exotics,
    }


def _make_srm_combo(
    leg_count: int,
    label: str,
    legs: List[Dict[str, Any]],
    fair_mult: float,
    discounted_odds: float,
    haircut: float,
) -> Dict[str, Any]:
    is_skipped = discounted_odds < MIN_SRM_ODDS
    skip_reason = (
        f"Skipped: Est. odds ${discounted_odds:.2f} < ${MIN_SRM_ODDS:.2f} min (low payout)"
        if is_skipped
        else None
    )
    return {
        "leg_count": leg_count,
        "label": label,
        "legs": legs,
        "raw_harville_odds": round(fair_mult, 2),
        "haircut_factor": haircut,
        "estimated_odds": discounted_odds,
        "unit_stake": 0.0 if is_skipped else UNIT_STAKE,
        "skipped": is_skipped,
        "skip_reason": skip_reason,
    }


def _build_srm_combos(runners: List[Dict[str, Any]], haircut: float) -> List[Dict[str, Any]]:
    combos: List[Dict[str, Any]] = []
    n = len(runners)

    # 2-leg SRM: #1 Top 2 + #2 Top 3
    if n >= 2:
        r1, r2 = runners[0], runners[1]
        raw_prob = max(0.01, r1["probabilities"]["top2"] * r2["probabilities"]["top3"])
        fair_mult = 1.0 / raw_prob
        discounted_odds = round(max(1.25, fair_mult * haircut), 2)
        combos.append(
            _make_srm_combo(
                leg_count=2,
                label="2-Leg SRM",
                legs=[
                    {"runner_number": r1["runner_number"], "name": r1["name"], "market": "Top 2", "prob": r1["probabilities"]["top2"]},
                    {"runner_number": r2["runner_number"], "name": r2["name"], "market": "Top 3", "prob": r2["probabilities"]["top3"]},
                ],
                fair_mult=fair_mult,
                discounted_odds=discounted_odds,
                haircut=haircut,
            )
        )

    # 3-leg SRM: #1 Top 2 + #2 Top 3 + #3 Top 4
    if n >= 3:
        r1, r2, r3 = runners[0], runners[1], runners[2]
        raw_prob = max(0.005, r1["probabilities"]["top2"] * r2["probabilities"]["top3"] * r3["probabilities"]["top4"])
        fair_mult = 1.0 / raw_prob
        discounted_odds = round(max(1.50, fair_mult * haircut), 2)
        combos.append(
            _make_srm_combo(
                leg_count=3,
                label="3-Leg SRM",
                legs=[
                    {"runner_number": r1["runner_number"], "name": r1["name"], "market": "Top 2", "prob": r1["probabilities"]["top2"]},
                    {"runner_number": r2["runner_number"], "name": r2["name"], "market": "Top 3", "prob": r2["probabilities"]["top3"]},
                    {"runner_number": r3["runner_number"], "name": r3["name"], "market": "Top 4", "prob": r3["probabilities"]["top4"]},
                ],
                fair_mult=fair_mult,
                discounted_odds=discounted_odds,
                haircut=haircut,
            )
        )

    # 4-leg SRM: #1 Top 2 + #2 Top 3 + #3 Top 4 + #4 Top 4
    if n >= 4:
        r1, r2, r3, r4 = runners[0], runners[1], runners[2], runners[3]
        raw_prob = max(
            0.002,
            r1["probabilities"]["top2"]
            * r2["probabilities"]["top3"]
            * r3["probabilities"]["top4"]
            * r4["probabilities"]["top4"],
        )
        fair_mult = 1.0 / raw_prob
        discounted_odds = round(max(1.50, fair_mult * haircut), 2)
        combos.append(
            _make_srm_combo(
                leg_count=4,
                label="4-Leg SRM",
                legs=[
                    {"runner_number": r1["runner_number"], "name": r1["name"], "market": "Top 2", "prob": r1["probabilities"]["top2"]},
                    {"runner_number": r2["runner_number"], "name": r2["name"], "market": "Top 3", "prob": r2["probabilities"]["top3"]},
                    {"runner_number": r3["runner_number"], "name": r3["name"], "market": "Top 4", "prob": r3["probabilities"]["top4"]},
                    {"runner_number": r4["runner_number"], "name": r4["name"], "market": "Top 4", "prob": r4["probabilities"]["top4"]},
                ],
                fair_mult=fair_mult,
                discounted_odds=discounted_odds,
                haircut=haircut,
            )
        )

    return combos


def _normalized_market_probs(runners: List[Dict[str, Any]]) -> Dict[int, float]:
    """Return normalized live market win probabilities keyed by runner_number (fallback to model win prob)."""
    raw: Dict[int, float] = {}
    for r in runners:
        rn = int(r["runner_number"])
        lv = float(r.get("live_win") or 0.0)
        if lv > 1.0:
            raw[rn] = 1.0 / lv
        else:
            raw[rn] = max(0.005, float((r.get("probabilities") or {}).get("win") or 0.01))
    total = sum(raw.values()) or 1.0
    return {rn: p / total for rn, p in raw.items()}


def _harville_ordered_prob(order: List[int], probs: Dict[int, float]) -> float:
    """Harville conditional probability of an exact finishing order (e.g. [1st, 2nd, 3rd])."""
    p_total = 1.0
    remaining = 1.0
    for rn in order:
        pr = max(1e-5, probs.get(rn, 0.01))
        p_total *= pr / max(remaining, 1e-5)
        remaining -= pr
        if remaining <= 1e-4:
            remaining = 1e-4
    return max(1e-6, min(0.99, p_total))


def _build_single_exotic(
    ex_key: str,
    label: str,
    legs: List[List[int]],
    combos_count: int,
    anchor_order: List[int],
    n_active: int,
    is_short_fav: bool,
    mkt_probs: Dict[int, float],
) -> Dict[str, Any]:
    min_req = MIN_EXOTIC_RUNNERS[ex_key]
    display = " / ".join(",".join(map(str, leg)) for leg in legs)
    structure = "Banker (Short Fav)" if is_short_fav else "Standard"

    if n_active < min_req:
        return {
            "type": label,
            "legs": legs,
            "combinations": combos_count,
            "flexi_pct": 0.0,
            "unit_stake": 0.0,
            "display": display,
            "structure": structure,
            "est_anchor_div": None,
            "est_anchor_return": None,
            "skipped": True,
            "skip_reason": f"Skipped: Only {n_active} active runners (min {min_req} required for {label})",
        }

    anchor_prob = _harville_ordered_prob(anchor_order, mkt_probs)
    est_anchor_div = round(TAB_EXOTIC_POOL_FACTOR / anchor_prob, 2)
    est_anchor_return = round(est_anchor_div * (UNIT_STAKE / max(combos_count, 1)), 2)
    flexi_pct = round((UNIT_STAKE / max(combos_count, 1)) * 100.0, 2)

    if est_anchor_return < MIN_EXOTIC_PROJECTED_RETURN:
        fav_tag = "short fav / " if is_short_fav else ""
        return {
            "type": label,
            "legs": legs,
            "combinations": combos_count,
            "flexi_pct": flexi_pct,
            "unit_stake": 0.0,
            "display": display,
            "structure": structure,
            "est_anchor_div": est_anchor_div,
            "est_anchor_return": est_anchor_return,
            "skipped": True,
            "skip_reason": (
                f"Skipped: Est. return ${est_anchor_return:.2f} < ${MIN_EXOTIC_PROJECTED_RETURN:.2f} min "
                f"({fav_tag}{combos_count} combos)"
            ),
        }

    return {
        "type": label,
        "legs": legs,
        "combinations": combos_count,
        "flexi_pct": flexi_pct,
        "unit_stake": UNIT_STAKE,
        "display": display,
        "structure": structure,
        "est_anchor_div": est_anchor_div,
        "est_anchor_return": est_anchor_return,
        "skipped": False,
        "skip_reason": None,
    }


def _build_exotics(runners: List[Dict[str, Any]]) -> Dict[str, Any]:
    nums = [int(r["runner_number"]) for r in runners]
    n = len(nums)
    if n < 2:
        return {"exacta": None, "trifecta": None, "first4": None}

    mkt_probs = _normalized_market_probs(runners)

    # Short-priced favourite anchored on top (< $2.30) triggers tight banker standout combos
    top1_live = float(runners[0].get("live_win") or 0.0)
    is_short_fav = bool(1.0 < top1_live < SHORT_FAV_ODDS_THRESHOLD)

    # Exacta: Standard 1,2 / 1,2,3 (4 combos · 250%) | Short Fav 1 / 2,3 (2 combos · 500%)
    if is_short_fav:
        ex_legs = [nums[:1], nums[1:3]]
    else:
        ex_legs = [nums[:2], nums[:3]]
    ex_combos = [(a, b) for a in ex_legs[0] for b in ex_legs[1] if a != b]
    exacta = _build_single_exotic(
        ex_key="exacta",
        label="Exacta",
        legs=ex_legs,
        combos_count=len(ex_combos),
        anchor_order=nums[:2],
        n_active=n,
        is_short_fav=is_short_fav,
        mkt_probs=mkt_probs,
    )

    # Trifecta: Standard 1,2 / 1,2,3,4 / 1,2,3,4,5 (18 combos · 55.6%) | Short Fav 1 / 2,3,4 / 2,3,4 (6 combos · 166.7%)
    if is_short_fav:
        tri_legs = [nums[:1], nums[1:4], nums[1:4]]
    else:
        tri_legs = [nums[:2], nums[:4], nums[:5]]
    tri_combos = [(a, b, c) for a in tri_legs[0] for b in tri_legs[1] for c in tri_legs[2] if len({a, b, c}) == 3]
    trifecta = _build_single_exotic(
        ex_key="trifecta",
        label="Trifecta",
        legs=tri_legs,
        combos_count=len(tri_combos),
        anchor_order=nums[:3],
        n_active=n,
        is_short_fav=is_short_fav,
        mkt_probs=mkt_probs,
    )

    # First 4: Standard 1,2 / 1,2,3,4 / 1,2,3,4 / 1,2,3,4,5 (24 combos · 41.7%)
    #          Short Fav 1 / 2,3 / 2,3,4 / 2,3,4,5 (8 combos · 125%)
    if is_short_fav:
        f4_legs = [nums[:1], nums[1:3], nums[1:4], nums[1:5]]
    else:
        f4_legs = [nums[:2], nums[:4], nums[:4], nums[:5]]
    f4_combos = [
        (a, b, c, d)
        for a in f4_legs[0]
        for b in f4_legs[1]
        for c in f4_legs[2]
        for d in f4_legs[3]
        if len({a, b, c, d}) == 4
    ]
    first4 = _build_single_exotic(
        ex_key="first4",
        label="First 4",
        legs=f4_legs,
        combos_count=len(f4_combos),
        anchor_order=nums[:4],
        n_active=n,
        is_short_fav=is_short_fav,
        mkt_probs=mkt_probs,
    )

    return {"exacta": exacta, "trifecta": trifecta, "first4": first4}


def build_meeting_quaddie(meeting: Dict[str, Any], meeting_races: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Generate a $10 flexi Quaddie ticket across the official 4 quaddie races."""
    q_nums = meeting.get("quaddie_races") or []
    if len(q_nums) != 4:
        # Default to last 4 races of the meeting if official quaddie list absent
        sorted_r = sorted(meeting_races, key=lambda r: int(r.get("race_number") or 0))
        if len(sorted_r) < 4:
            return None
        q_nums = [int(r["race_number"]) for r in sorted_r[-4:]]

    race_by_num = {int(r.get("race_number") or 0): r for r in meeting_races}
    legs = []
    total_combos = 1
    for rnum in q_nums:
        r = race_by_num.get(int(rnum))
        if not r:
            return None
        top_runners = (r.get("top_10") or [])[:3]
        if not top_runners:
            return None
        # Take top 2 if clear standout (win prob >= 30%), else top 3
        pick_count = 2 if float(top_runners[0]["probabilities"]["win"]) >= 0.30 else min(3, len(top_runners))
        selected = [int(x["runner_number"]) for x in top_runners[:pick_count]]
        total_combos *= len(selected)
        legs.append(
            {
                "race_id": r["race_id"],
                "race_number": int(rnum),
                "race_name": r.get("name") or f"Race {rnum}",
                "runner_numbers": selected,
                "runner_names": [x["name"] for x in top_runners[:pick_count]],
            }
        )

    return {
        "meeting_slug": meeting.get("meeting_slug"),
        "meeting_name": meeting.get("display_name") or meeting.get("name"),
        "date": meeting.get("date"),
        "quaddie_races": q_nums,
        "legs": legs,
        "combinations": total_combos,
        "flexi_pct": round((UNIT_STAKE / max(total_combos, 1)) * 100.0, 2),
        "unit_stake": UNIT_STAKE,
        "display": " / ".join(",".join(map(str, leg["runner_numbers"])) for leg in legs),
    }
