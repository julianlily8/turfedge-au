from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipeline.src.engine.gemini_batch import GeminiBatchAnalyzer
from pipeline.src.engine.pricing import SRM_CORRELATION_HAIRCUT, build_meeting_quaddie, price_and_generate_bets
from pipeline.src.engine.scorer_23pt import NEUTRAL_SCORE, NO_DATA_LABEL, compute_market_fluc, score_race_23pt
from pipeline.src.engine.settlement import settle_race
from pipeline.src.http_client import PoliteHttpClient


def _sample_race(field_size: int = 9):
    runners = []
    for i in range(1, field_size + 1):
        runners.append(
            {
                "runner_number": i,
                "name": f"Runner {i}",
                "barrier": i,
                "is_scratched": False,
                "jockey": "James McDonald" if i == 1 else f"Jockey {i}",
                "trainer": "Chris Waller" if i == 1 else f"Trainer {i}",
                "apprentice_claim_kg": 2.0 if i == 2 else 0.0,
                "weight_allocated": 58.0,
                "weight_carried": 56.0 if i == 2 else 58.0,
                "gear": "Blinkers First Time" if i == 1 else "",
                "sire": "I Am Invincible" if i == 1 else "Savabeel",
                "dam": "Test Dam",
                "prize_money": 250000 if i == 1 else 40000,
                "last_twenty_starts": "x1121" if i == 1 else "54678",
                "opening_win": 5.0 if i == 1 else 10.0 + i,
                "live_win": 4.2 if i == 1 else 12.0 + i,
                "live_place": 1.8 if i == 1 else 3.5,
                "speedmap_label": "On-Pace" if i <= 2 else "Backmarker",
                "settling_lengths": 2.0 if i <= 2 else 8.0,
                "form_comment": "",
                "stewards_notes": [],
                "vet_notes": [],
                "trial_runs": [],
                "past_performances": [],
                "jockey_past_performances": [],
            }
        )
    return {
        "race_id": "test-race-1",
        "date": "2026-11-25",
        "meeting_slug": "randwick",
        "meeting_name": "Royal Randwick",
        "tier": "Metro",
        "race_number": 1,
        "name": "Test Sprint 1200m",
        "distance": 1200,
        "distance_band": "Sprint",
        "track_condition": "Good4",
        "track_condition_band": "Good",
        "rail_position": "True",
        "race_comment": "",
        "prize_money": 120000,
        "runners": runners,
    }


def test_ladbrokes_headers_and_min_delay():
    client = PoliteHttpClient(min_delay_seconds=1.0)  # enforced floor is 3.0s
    assert client.min_delay_seconds == 3.0
    headers = client._build_headers("https://api.ladbrokes.com.au/affiliates/v1/racing/meetings")
    assert "From" in headers and "@" in headers["From"]
    assert "X-Partner" in headers
    assert "TurfEdgeAU" in headers["User-Agent"]


def test_market_fluc_calculation():
    firmer = compute_market_fluc(opening_win=5.0, live_win=3.5)
    assert firmer["direction"] == "Firmer"
    assert firmer["implied_prob_delta_pct"] > 0
    assert firmer["fluc_pct"] > 0

    drifter = compute_market_fluc(opening_win=3.0, live_win=5.0)
    assert drifter["direction"] == "Drifter"
    assert drifter["implied_prob_delta_pct"] < 0


def test_strict_neutral_fallback_and_anti_hallucination_guard():
    race = _sample_race()
    track_meta = {"name": "Royal Randwick", "home_straight_m": 410, "turning": "Clockwise"}
    weather = {"source": "BOM Hourly API", "headwind_kmh": 0.0, "wind_speed_kmh": 5.0, "rain_expected_mm": 0.0, "effect": "Calm"}

    scored = score_race_23pt(race, track_meta, weather)
    assert len(scored) == 9
    assert len(scored[0]["audit_23pt"]) == 23

    # Verify missing text fields are strictly 5.0 / "No data available"
    audit_map = {a["key"]: a for a in scored[0]["audit_23pt"]}
    for key in ("recent_trials", "injury_history", "stewards_comments", "trainer_jockey_commentary", "youtube_transcripts"):
        assert audit_map[key]["score"] == NEUTRAL_SCORE
        assert audit_map[key]["reason"] == NO_DATA_LABEL
        assert audit_map[key]["has_data"] is False

    # Verify Gemini anti-hallucination guard blocks fabricated overrides on missing fields
    race["scored_runners"] = scored
    analyzer = GeminiBatchAnalyzer(api_key="dummy")
    hallucinated_ai = {
        "race_id": "test-race-1",
        "runners": [
            {
                "runner_number": 1,
                "summary_note": "Strong sprint profile",
                "factor_overrides": {
                    "stewards_comments": {"score": 9.5, "reason": "Fabricated stewards quote"},
                    "injury_history": {"score": 1.0, "reason": "Fabricated tendon tear"},
                    "trainer_jockey_commentary": {"score": 9.0, "reason": "Fabricated trainer quote"},
                },
            }
        ],
    }
    analyzer._apply_validated_overrides(race, hallucinated_ai, youtube_transcript="")
    r1 = next(r for r in race["scored_runners"] if r["runner_number"] == 1)
    r1_map = {a["key"]: a for a in r1["audit_23pt"]}
    assert r1_map["stewards_comments"]["score"] == NEUTRAL_SCORE
    assert r1_map["stewards_comments"]["reason"] == NO_DATA_LABEL
    assert r1_map["injury_history"]["score"] == NEUTRAL_SCORE
    assert r1_map["trainer_jockey_commentary"]["reason"] == NO_DATA_LABEL


def test_pricing_overlay_harville_and_srm_haircut():
    race = _sample_race()
    track_meta = {"name": "Royal Randwick", "home_straight_m": 410, "turning": "Clockwise"}
    weather = {"source": "BOM Hourly API", "headwind_kmh": 0.0, "wind_speed_kmh": 5.0, "rain_expected_mm": 0.0, "effect": "Calm"}
    race["scored_runners"] = score_race_23pt(race, track_meta, weather)

    # Set live_win artificially high on top runner to verify strict Value Overlay trigger
    race["scored_runners"][0]["live_win"] = 8.0
    pricing = price_and_generate_bets(race)

    ws = pricing["win_selection"]
    assert ws["is_value_overlay"] is (ws["live_price"] > ws["rated_price"])
    assert ws["is_value_overlay"] is True

    # Monotonicity of Harville probabilities: win <= top2 <= top3 <= top4 <= top10
    for entry in pricing["top_10"]:
        p = entry["probabilities"]
        assert p["win"] <= p["top2"] <= p["top3"] <= p["top4"] <= p["top10"]

    # Verify 0.75x SRM correlation haircut
    assert len(pricing["srm_combos"]) == 3
    for srm in pricing["srm_combos"]:
        assert srm["haircut_factor"] == SRM_CORRELATION_HAIRCUT
        assert srm["estimated_odds"] <= srm["raw_harville_odds"]


def test_settlement_math_and_srm_haircut():
    race = _sample_race()
    track_meta = {"name": "Royal Randwick", "home_straight_m": 410, "turning": "Clockwise"}
    weather = {"source": "BOM Hourly API", "headwind_kmh": 0.0, "wind_speed_kmh": 5.0, "rain_expected_mm": 0.0, "effect": "Calm"}
    race["scored_runners"] = score_race_23pt(race, track_meta, weather)
    race["scored_runners"][0]["live_win"] = 6.0
    pricing = price_and_generate_bets(race)
    race.update(pricing)

    top_nums = [r["runner_number"] for r in race["top_10"][:4]]
    results = [
        {"position": 1, "runner_number": top_nums[0], "name": f"Runner {top_nums[0]}", "barrier": 1, "margin_length": 0.0},
        {"position": 2, "runner_number": top_nums[1], "name": f"Runner {top_nums[1]}", "barrier": 2, "margin_length": 0.5},
        {"position": 3, "runner_number": top_nums[2], "name": f"Runner {top_nums[2]}", "barrier": 3, "margin_length": 1.0},
        {"position": 4, "runner_number": top_nums[3], "name": f"Runner {top_nums[3]}", "barrier": 4, "margin_length": 1.5},
    ]
    dividends = {
        "best_tote_win": {str(top_nums[0]): 5.5},
        "exacta": 18.0,
        "trifecta": 85.0,
        "first4": 240.0,
    }
    bsp = {f"runner{top_nums[0]}": {"win_bsp": 6.2, "place_bsp": 2.1}}
    settled = settle_race(race, results, dividends, bsp_lookup=bsp)

    assert settled["win_all"]["won"] is True
    assert settled["win_all"]["return"] == 55.0
    assert settled["win_all"]["profit"] == 45.0
    assert settled["win_all"]["betfair_bsp"] == 6.2
    assert settled["win_overlay"]["won"] is True
    assert settled["exacta"]["won"] is True
    assert settled["trifecta"]["won"] is True
    assert settled["first4"]["won"] is True
    assert all(s["won"] is True for s in settled["srm"])
    assert all(s["haircut_factor"] == 0.75 for s in settled["srm"])


def test_go_timestamp_parsing_and_betfair_403_resilience():
    from datetime import datetime, timezone
    import requests
    from pipeline.src.jobs.pre_jump_analyze import _minutes_until_jump
    from pipeline.src.jobs.post_race_settle import _race_has_jumped
    from pipeline.src.sources.betfair_bsp import BetfairBspSource

    now = datetime(2026, 11, 25, 3, 40, 0, tzinfo=timezone.utc)
    # Ladbrokes Go-formatted UTC string
    mins = _minutes_until_jump("2026-11-25 04:05:00 +0000 UTC", now)
    assert mins == 25.0
    assert _race_has_jumped("2026-11-25 03:30:00 +0000 UTC", now) is True
    assert _race_has_jumped("2026-11-25 04:05:00 +0000 UTC", now) is False

    # Betfair 403 Forbidden (unpublished CSV or geo-block) must degrade gracefully to {}
    mock_http = MagicMock()
    mock_http.get_text.side_effect = requests.HTTPError("403 Client Error: Forbidden")
    bf = BetfairBspSource(mock_http)
    assert bf.fetch_daily_bsp("2026-11-25") == {}


def test_first_four_dividend_parsing_and_scratched_voiding():
    from pipeline.src.sources.ladbrokes import LadbrokesSource

    # Verify all First 4 / First Four / Superfecta dividend naming variants
    divs = LadbrokesSource._parse_dividends(
        [
            {"product_name": "First Four", "dividend": 148.1},
            {"product_name": "FIRST_4", "dividend": 152.5},
            {"product_name": "SUPERFECTA", "dividend": 140.0},
            {"product_name": "Exacta", "dividend": 20.3},
            {"product_name": "Trifecta", "dividend": 51.4},
            {"product_name": "Quadrella", "dividend": 420.0},
        ]
    )
    assert divs["first4"] == 152.5
    assert divs["exacta"] == 20.3
    assert divs["trifecta"] == 51.4
    assert divs["quaddie"] == 420.0

    # Verify scratched / missing-position runner voids Win + affected SRM legs ($0 stake / $0 return)
    race = _sample_race()
    track_meta = {"name": "Royal Randwick", "home_straight_m": 410, "turning": "Clockwise"}
    weather = {"source": "BOM Hourly API", "headwind_kmh": 0.0, "wind_speed_kmh": 5.0, "rain_expected_mm": 0.0, "effect": "Calm"}
    race["scored_runners"] = score_race_23pt(race, track_meta, weather)
    race.update(price_and_generate_bets(race))

    top_num = race["win_selection"]["runner_number"]
    other_nums = [r["runner_number"] for r in race["scored_runners"] if r["runner_number"] != top_num]
    # Top selection scratched (absent from results)
    results = [
        {"position": idx + 1, "runner_number": rn, "name": f"Runner {rn}", "barrier": idx + 1, "margin_length": idx * 0.5}
        for idx, rn in enumerate(other_nums)
    ]
    settled = settle_race(race, results, divs, scratched_numbers={top_num})
    assert settled["win_all"]["void"] is True
    assert settled["win_all"]["scratched"] is True
    assert settled["win_all"]["stake"] == 0.0
    assert settled["win_all"]["return"] == 0.0
    assert settled["win_all"]["profit"] == 0.0
    # Every SRM combo includes top_num as leg 1, so all must be voided
    assert all(s["void"] is True and s["stake"] == 0.0 and s["profit"] == 0.0 for s in settled["srm"])


def test_exotic_field_size_short_fav_and_harville_return_gates():
    track_meta = {"name": "Royal Randwick", "home_straight_m": 410, "turning": "Clockwise"}
    weather = {"source": "BOM Hourly API", "headwind_kmh": 0.0, "wind_speed_kmh": 5.0, "rain_expected_mm": 0.0, "effect": "Calm"}

    # 1) Small 5-runner field -> all 3 exotics skipped by field-size gate
    small_race = _sample_race(field_size=5)
    small_race["scored_runners"] = score_race_23pt(small_race, track_meta, weather)
    p_small = price_and_generate_bets(small_race)
    assert p_small["exotics"]["exacta"]["skipped"] is True
    assert p_small["exotics"]["trifecta"]["skipped"] is True
    assert p_small["exotics"]["first4"]["skipped"] is True

    # 2) Standard 9-runner field (fav >= $2.30) -> 4 / 18 / 24 combos
    std_race = _sample_race(field_size=9)
    std_race["scored_runners"] = score_race_23pt(std_race, track_meta, weather)
    p_std = price_and_generate_bets(std_race)
    assert p_std["exotics"]["exacta"]["combinations"] == 4
    assert p_std["exotics"]["trifecta"]["combinations"] == 18
    assert p_std["exotics"]["first4"]["combinations"] == 24
    assert p_std["exotics"]["exacta"]["skipped"] is False

    # 3) Dominant short-priced favourite (< $2.30) in 9-runner field -> 2 / 6 / 8 banker combos
    short_race = _sample_race(field_size=9)
    short_race["runners"][0]["live_win"] = 1.20
    short_race["runners"][0]["opening_win"] = 1.40
    short_race["runners"][1]["live_win"] = 2.40
    short_race["runners"][1]["opening_win"] = 2.60
    for r in short_race["runners"][2:]:
        r["live_win"] = 41.0
        r["opening_win"] = 41.0
    short_race["scored_runners"] = score_race_23pt(short_race, track_meta, weather)
    p_short = price_and_generate_bets(short_race)
    assert p_short["exotics"]["exacta"]["combinations"] == 2
    assert p_short["exotics"]["trifecta"]["combinations"] == 6
    assert p_short["exotics"]["first4"]["combinations"] == 8
    # Exacta on $1.20 -> $2.40 against $41 outsiders has projected return < $12.50 -> must be skipped!
    assert p_short["exotics"]["exacta"]["skipped"] is True
    assert p_short["exotics"]["exacta"]["est_anchor_return"] < 12.50
    assert "Est. return" in p_short["exotics"]["exacta"]["skip_reason"]

    # Skipped exacta must NOT be staked or settled in settlement.py
    short_race.update(p_short)
    top_nums = [r["runner_number"] for r in short_race["top_10"][:4]]
    results = [
        {"position": idx + 1, "runner_number": rn, "name": f"Runner {rn}", "barrier": idx + 1, "margin_length": idx * 0.5}
        for idx, rn in enumerate(top_nums)
    ]
    settled = settle_race(short_race, results, {"best_tote_win": {str(top_nums[0]): 1.4}, "exacta": 3.2})
    assert settled["exacta"] is None
