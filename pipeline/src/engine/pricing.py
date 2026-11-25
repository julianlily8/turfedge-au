from __future__ import annotations

import itertools
import math
from typing import Any, Dict, List, Optional

from pipeline.src.engine.scorer_23pt import load_factor_weights

SRM_CORRELATION_HAIRCUT = 0.75
UNIT_STAKE = 10.0


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
    else:
        blended = model_probs

    b_sum = sum(blended) or 1.0
    win_probs = [p / b_sum for p in blended]
    harville = _harville_top_k_probs(win_probs)

    for idx, (r, h) in enumerate(zip(runners, harville)):
        rated_price = round(min(151.0, max(1.05, 1.0 / max(h["win"], 1e-4))), 2)
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
            {
                "leg_count": 2,
                "label": "2-Leg SRM",
                "legs": [
                    {"runner_number": r1["runner_number"], "name": r1["name"], "market": "Top 2", "prob": r1["probabilities"]["top2"]},
                    {"runner_number": r2["runner_number"], "name": r2["name"], "market": "Top 3", "prob": r2["probabilities"]["top3"]},
                ],
                "raw_harville_odds": round(fair_mult, 2),
                "haircut_factor": haircut,
                "estimated_odds": discounted_odds,
                "unit_stake": UNIT_STAKE,
            }
        )

    # 3-leg SRM: #1 Top 2 + #2 Top 3 + #3 Top 4
    if n >= 3:
        r1, r2, r3 = runners[0], runners[1], runners[2]
        raw_prob = max(0.005, r1["probabilities"]["top2"] * r2["probabilities"]["top3"] * r3["probabilities"]["top4"])
        fair_mult = 1.0 / raw_prob
        discounted_odds = round(max(1.5, fair_mult * haircut), 2)
        combos.append(
            {
                "leg_count": 3,
                "label": "3-Leg SRM",
                "legs": [
                    {"runner_number": r1["runner_number"], "name": r1["name"], "market": "Top 2", "prob": r1["probabilities"]["top2"]},
                    {"runner_number": r2["runner_number"], "name": r2["name"], "market": "Top 3", "prob": r2["probabilities"]["top3"]},
                    {"runner_number": r3["runner_number"], "name": r3["name"], "market": "Top 4", "prob": r3["probabilities"]["top4"]},
                ],
                "raw_harville_odds": round(fair_mult, 2),
                "haircut_factor": haircut,
                "estimated_odds": discounted_odds,
                "unit_stake": UNIT_STAKE,
            }
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
        discounted_odds = round(max(2.0, fair_mult * haircut), 2)
        combos.append(
            {
                "leg_count": 4,
                "label": "4-Leg SRM",
                "legs": [
                    {"runner_number": r1["runner_number"], "name": r1["name"], "market": "Top 2", "prob": r1["probabilities"]["top2"]},
                    {"runner_number": r2["runner_number"], "name": r2["name"], "market": "Top 3", "prob": r2["probabilities"]["top3"]},
                    {"runner_number": r3["runner_number"], "name": r3["name"], "market": "Top 4", "prob": r3["probabilities"]["top4"]},
                    {"runner_number": r4["runner_number"], "name": r4["name"], "market": "Top 4", "prob": r4["probabilities"]["top4"]},
                ],
                "raw_harville_odds": round(fair_mult, 2),
                "haircut_factor": haircut,
                "estimated_odds": discounted_odds,
                "unit_stake": UNIT_STAKE,
            }
        )

    return combos


def _build_exotics(runners: List[Dict[str, Any]]) -> Dict[str, Any]:
    nums = [int(r["runner_number"]) for r in runners]
    n = len(nums)

    # Exacta: Standout top 2 / top 3 (4 combinations when n>=3)
    exacta = None
    if n >= 3:
        leg1 = nums[:2]
        leg2 = nums[:3]
        combos = [(a, b) for a in leg1 for b in leg2 if a != b]
        exacta = {
            "type": "Exacta",
            "legs": [leg1, leg2],
            "combinations": len(combos),
            "flexi_pct": round((UNIT_STAKE / max(len(combos), 1)) * 100.0, 2),
            "unit_stake": UNIT_STAKE,
            "display": f"{','.join(map(str, leg1))} / {','.join(map(str, leg2))}",
        }

    # Trifecta: Top 2 / Top 4 / Top 5
    trifecta = None
    if n >= 4:
        l1 = nums[:2]
        l2 = nums[: min(4, n)]
        l3 = nums[: min(5, n)]
        combos = [(a, b, c) for a in l1 for b in l2 for c in l3 if len({a, b, c}) == 3]
        trifecta = {
            "type": "Trifecta",
            "legs": [l1, l2, l3],
            "combinations": len(combos),
            "flexi_pct": round((UNIT_STAKE / max(len(combos), 1)) * 100.0, 2),
            "unit_stake": UNIT_STAKE,
            "display": f"{','.join(map(str, l1))} / {','.join(map(str, l2))} / {','.join(map(str, l3))}",
        }

    # First 4: Top 2 / Top 4 / Top 5 / Top 6
    first4 = None
    if n >= 5:
        l1 = nums[:2]
        l2 = nums[: min(4, n)]
        l3 = nums[: min(5, n)]
        l4 = nums[: min(6, n)]
        combos = [
            (a, b, c, d)
            for a in l1
            for b in l2
            for c in l3
            for d in l4
            if len({a, b, c, d}) == 4
        ]
        first4 = {
            "type": "First 4",
            "legs": [l1, l2, l3, l4],
            "combinations": len(combos),
            "flexi_pct": round((UNIT_STAKE / max(len(combos), 1)) * 100.0, 2),
            "unit_stake": UNIT_STAKE,
            "display": f"{','.join(map(str, l1))} / {','.join(map(str, l2))} / {','.join(map(str, l3))} / {','.join(map(str, l4))}",
        }

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
