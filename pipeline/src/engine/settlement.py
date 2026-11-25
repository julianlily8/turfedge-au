from __future__ import annotations

from typing import Any, Dict, List, Optional

from pipeline.src.engine.pricing import SRM_CORRELATION_HAIRCUT, UNIT_STAKE
from pipeline.src.sources.betfair_bsp import normalize_horse_name


def _finish_position_map(results: List[Dict[str, Any]]) -> Dict[int, int]:
    return {int(r["runner_number"]): int(r["position"]) for r in results if int(r.get("position") or 0) > 0}


def _srm_leg_won(pos: Optional[int], market: str) -> bool:
    if pos is None or pos <= 0:
        return False
    m = (market or "").lower()
    if "top 2" in m:
        return pos <= 2
    if "top 3" in m:
        return pos <= 3
    if "top 4" in m:
        return pos <= 4
    if "top 10" in m:
        return pos <= 10
    return pos == 1


def settle_race(
    race: Dict[str, Any],
    results: List[Dict[str, Any]],
    dividends: Dict[str, Any],
    bsp_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Settle all pre-race recommendations on standardized $10 unit stakes.
    - Applies conservative 0.75x correlation haircut on SRM Harville odds.
    - Preserves actual_odds_override hook for manual settlement edits in Tab 2.
    - Attaches Betfair BSP Win/Place prices when published.
    """
    pos_map = _finish_position_map(results)
    bsp_lookup = bsp_lookup or {}

    top4_order = [r["runner_number"] for r in sorted(results, key=lambda x: x["position"])[:4]]
    best_tote_win = dividends.get("best_tote_win") or {}

    win_sel = race.get("win_selection")
    win_bet = None
    overlay_bet = None

    if win_sel:
        rn = int(win_sel["runner_number"])
        won = pos_map.get(rn) == 1
        tote_div = float(best_tote_win.get(str(rn)) or 0.0)
        sp_div = tote_div if tote_div > 1.0 else float(win_sel.get("live_price") or win_sel.get("opening_price") or 0.0)

        bsp_entry = bsp_lookup.get(normalize_horse_name(win_sel.get("name") or ""))
        win_bsp = bsp_entry.get("win_bsp") if bsp_entry else None
        effective_odds = sp_div if sp_div > 1.0 else (win_bsp or 0.0)

        ret = round(UNIT_STAKE * effective_odds, 2) if (won and effective_odds > 0) else 0.0
        bsp_ret = round(UNIT_STAKE * win_bsp, 2) if (won and win_bsp) else (0.0 if win_bsp else None)

        win_bet = {
            "category": "win_all",
            "runner_number": rn,
            "runner_name": win_sel["name"],
            "stake": UNIT_STAKE,
            "won": won,
            "finish_position": pos_map.get(rn),
            "settled_odds": round(effective_odds, 2),
            "tote_sp": round(tote_div, 2) if tote_div > 0 else None,
            "betfair_bsp": win_bsp,
            "actual_odds_override": None,
            "return": ret,
            "profit": round(ret - UNIT_STAKE, 2),
            "bsp_return": bsp_ret,
            "bsp_profit": round(bsp_ret - UNIT_STAKE, 2) if bsp_ret is not None else None,
            "is_value_overlay": bool(win_sel.get("is_value_overlay")),
            "rated_price": win_sel.get("rated_price"),
            "live_price": win_sel.get("live_price"),
            "fluc_direction": (win_sel.get("market_fluc") or {}).get("direction", "Steady"),
            "fluc_pct": (win_sel.get("market_fluc") or {}).get("fluc_pct", 0.0),
        }

        if win_sel.get("is_value_overlay"):
            overlay_bet = dict(win_bet, category="win_overlay")

    # Same Race Multi settlement (with 0.75x correlation haircut)
    srm_settlements: List[Dict[str, Any]] = []
    for combo in race.get("srm_combos") or []:
        legs = combo.get("legs") or []
        all_won = bool(legs) and all(_srm_leg_won(pos_map.get(int(leg["runner_number"])), leg["market"]) for leg in legs)
        raw_harville = float(combo.get("raw_harville_odds") or combo.get("estimated_odds") or 1.0)
        haircut = float(combo.get("haircut_factor") or SRM_CORRELATION_HAIRCUT)
        est_odds = round(float(combo.get("estimated_odds") or max(1.25, raw_harville * haircut)), 2)
        ret = round(UNIT_STAKE * est_odds, 2) if all_won else 0.0
        srm_settlements.append(
            {
                "category": f"srm_{combo['leg_count']}leg",
                "leg_count": int(combo["leg_count"]),
                "label": combo.get("label") or f"{combo['leg_count']}-Leg SRM",
                "legs": legs,
                "stake": UNIT_STAKE,
                "won": all_won,
                "raw_harville_odds": round(raw_harville, 2),
                "haircut_factor": haircut,
                "settled_odds": est_odds,
                "actual_odds_override": None,
                "return": ret,
                "profit": round(ret - UNIT_STAKE, 2),
            }
        )

    exotics = race.get("exotics") or {}
    exotic_settlements: Dict[str, Any] = {}

    # Exacta ($10 flexi)
    ex = exotics.get("exacta")
    if ex and len(top4_order) >= 2:
        l1, l2 = ex["legs"][0], ex["legs"][1]
        won = (top4_order[0] in l1) and (top4_order[1] in l2)
        div = float(dividends.get("exacta") or 0.0)
        flexi = float(ex["flexi_pct"]) / 100.0
        ret = round(div * flexi, 2) if (won and div > 0) else 0.0
        exotic_settlements["exacta"] = {
            "category": "exacta",
            "display": ex["display"],
            "combinations": ex["combinations"],
            "flexi_pct": ex["flexi_pct"],
            "stake": UNIT_STAKE,
            "won": won,
            "dividend": div if div > 0 else None,
            "actual_odds_override": None,
            "return": ret,
            "profit": round(ret - UNIT_STAKE, 2),
        }

    # Trifecta ($10 flexi)
    tri = exotics.get("trifecta")
    if tri and len(top4_order) >= 3:
        l1, l2, l3 = tri["legs"][0], tri["legs"][1], tri["legs"][2]
        won = (top4_order[0] in l1) and (top4_order[1] in l2) and (top4_order[2] in l3)
        div = float(dividends.get("trifecta") or 0.0)
        flexi = float(tri["flexi_pct"]) / 100.0
        ret = round(div * flexi, 2) if (won and div > 0) else 0.0
        exotic_settlements["trifecta"] = {
            "category": "trifecta",
            "display": tri["display"],
            "combinations": tri["combinations"],
            "flexi_pct": tri["flexi_pct"],
            "stake": UNIT_STAKE,
            "won": won,
            "dividend": div if div > 0 else None,
            "actual_odds_override": None,
            "return": ret,
            "profit": round(ret - UNIT_STAKE, 2),
        }

    # First 4 ($10 flexi)
    f4 = exotics.get("first4")
    if f4 and len(top4_order) >= 4:
        l1, l2, l3, l4 = f4["legs"][0], f4["legs"][1], f4["legs"][2], f4["legs"][3]
        won = (
            (top4_order[0] in l1)
            and (top4_order[1] in l2)
            and (top4_order[2] in l3)
            and (top4_order[3] in l4)
        )
        div = float(dividends.get("first4") or 0.0)
        flexi = float(f4["flexi_pct"]) / 100.0
        ret = round(div * flexi, 2) if (won and div > 0) else 0.0
        exotic_settlements["first4"] = {
            "category": "first4",
            "display": f4["display"],
            "combinations": f4["combinations"],
            "flexi_pct": f4["flexi_pct"],
            "stake": UNIT_STAKE,
            "won": won,
            "dividend": div if div > 0 else None,
            "actual_odds_override": None,
            "return": ret,
            "profit": round(ret - UNIT_STAKE, 2),
        }

    # Capture top runner's 23-point factor scores so Tab 2 can run Factor Attribution diagnostics!
    top_runner = (race.get("scored_runners") or [None])[0]
    factor_scores = (
        {a["key"]: a["score"] for a in top_runner.get("audit_23pt", []) if a.get("has_data")}
        if top_runner
        else {}
    )

    return {
        "settlement_id": race["race_id"],
        "kind": "race",
        "race_id": race["race_id"],
        "date": race.get("date"),
        "meeting_slug": race.get("meeting_slug"),
        "meeting_name": race.get("meeting_name"),
        "tier": race.get("tier", "Provincial"),
        "race_number": race.get("race_number"),
        "race_name": race.get("name"),
        "track_condition": race.get("track_condition"),
        "track_condition_band": race.get("track_condition_band", "Good"),
        "distance": race.get("distance", 0),
        "distance_band": race.get("distance_band", "Sprint"),
        "fluc_direction": (win_bet or {}).get("fluc_direction", "Steady"),
        "top4_order": top4_order,
        "win_all": win_bet,
        "win_overlay": overlay_bet,
        "srm": srm_settlements,
        "exacta": exotic_settlements.get("exacta"),
        "trifecta": exotic_settlements.get("trifecta"),
        "first4": exotic_settlements.get("first4"),
        "quaddie": None,
        "top_runner_factor_scores": factor_scores,
        "bsp_reconciled": bool(win_bet and win_bet.get("betfair_bsp") is not None),
    }


def settle_meeting_quaddie(
    meeting: Dict[str, Any],
    meeting_races: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Settle meeting-level $10 flexi Quaddie once all 4 official Quaddie legs are finalized."""
    ticket = meeting.get("quaddie_ticket")
    if not ticket:
        return None
    legs = ticket.get("legs") or []
    if len(legs) != 4:
        return None

    race_by_id = {r["race_id"]: r for r in meeting_races}
    leg_hits = []
    quaddie_div = None

    for leg in legs:
        r = race_by_id.get(leg["race_id"])
        if not r or not (r.get("results") or []):
            return None  # Not all 4 legs finished yet
        winner_num = int(r["results"][0]["runner_number"])
        leg_hits.append(winner_num in [int(x) for x in leg["runner_numbers"]])
        q_div = (r.get("dividends") or {}).get("quaddie")
        if q_div and float(q_div) > 0:
            quaddie_div = float(q_div)

    won = all(leg_hits)
    flexi = float(ticket["flexi_pct"]) / 100.0
    ret = round(quaddie_div * flexi, 2) if (won and quaddie_div) else 0.0

    return {
        "settlement_id": f"quaddie_{meeting['date']}_{meeting['meeting_slug']}",
        "kind": "quaddie",
        "date": meeting["date"],
        "meeting_slug": meeting["meeting_slug"],
        "meeting_name": meeting.get("display_name") or meeting.get("name"),
        "tier": meeting.get("tier", "Provincial"),
        "track_condition": meeting.get("track_condition"),
        "track_condition_band": meeting.get("track_condition_band", "Good"),
        "distance_band": "All",
        "fluc_direction": "All",
        "quaddie": {
            "category": "quaddie",
            "display": ticket["display"],
            "combinations": ticket["combinations"],
            "flexi_pct": ticket["flexi_pct"],
            "legs_won": sum(1 for h in leg_hits if h),
            "stake": UNIT_STAKE,
            "won": won,
            "dividend": quaddie_div,
            "actual_odds_override": None,
            "return": ret,
            "profit": round(ret - UNIT_STAKE, 2),
        },
    }
