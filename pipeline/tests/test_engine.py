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


def test_youtube_xml_feed_discovery_and_runner_snippet_extraction():
    from datetime import datetime, timezone
    from pipeline.src.sources.youtube_feed import (
        extract_runner_relevant_snippets,
        match_videos_to_meeting,
        parse_youtube_atom_feed,
    )

    sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <yt:videoId>vidFreshHaw1</yt:videoId>
        <title>Hawkesbury Full Preview &amp; Best Bets | Thursday Racing</title>
        <published>2026-11-25T22:00:00+00:00</published>
      </entry>
      <entry>
        <yt:videoId>vidStaleHaw2</yt:videoId>
        <title>Hawkesbury Old Preview From Last Week</title>
        <published>2026-11-20T05:00:00+00:00</published>
      </entry>
      <entry>
        <yt:videoId>vidFreshFlem</yt:videoId>
        <title>Flemington Track Walk and Quaddie Tips</title>
        <published>2026-11-25T18:30:00+00:00</published>
      </entry>
    </feed>"""

    now = datetime(2026, 11, 26, 0, 0, 0, tzinfo=timezone.utc)
    recent = parse_youtube_atom_feed(sample_xml, "Racing.com", now_utc=now, lookback_hours=36)
    assert len(recent) == 2
    assert {v["video_id"] for v in recent} == {"vidFreshHaw1", "vidFreshFlem"}

    matched = match_videos_to_meeting(recent, "Hawkesbury", aliases=["Hawkesbury"])
    assert len(matched) == 1
    assert matched[0]["video_id"] == "vidFreshHaw1"

    long_transcript = ("intro chatter " * 200) + "In Race 3 we really love Harbour Sovereign off a sharp trial. " + ("outro filler " * 200)
    snippet = extract_runner_relevant_snippets(long_transcript, ["Harbour Sovereign"], max_total_chars=400)
    assert "Harbour Sovereign" in snippet
    assert len(snippet) <= 400

    # Strict upper_bound_utc cutoff must exclude videos published after Race 1 cutoff
    from pipeline.src.jobs.rebuild_date_blind import scrub_event_blind

    cutoff = datetime(2026, 11, 25, 1, 0, 0, tzinfo=timezone.utc)
    pre_cutoff = parse_youtube_atom_feed(sample_xml, "Racing.com", lookback_hours=60, upper_bound_utc=cutoff)
    assert pre_cutoff == []  # All entries in sample_xml were after 2026-11-25T01:00:00Z or >60h old

    dirty_ev = {
        "status": "Final",
        "results": [{"position": 1, "runner_number": 1}],
        "dividends": {"exacta": 12.5},
        "race_comment": "Runner 1 won easily.",
        "runners": [
            {"runner_number": 1, "name": "Test Horse", "finishing_position": 1, "margin": 0.0, "form_comment": "Won by 2L"}
        ],
    }
    clean = scrub_event_blind(dirty_ev)
    assert clean["status"] == "Open"
    assert clean["results"] == []
    assert clean["dividends"] == {}
    assert clean["race_comment"] == ""
    assert "finishing_position" not in clean["runners"][0]
    assert "margin" not in clean["runners"][0]
    assert clean["runners"][0]["form_comment"] == ""


def test_progressive_bias_free_current_meeting_bias():
    from pipeline.src.engine.scorer_23pt import apply_progressive_meeting_bias

    track_meta = {"name": "Royal Randwick", "home_straight_m": 410, "turning": "Clockwise"}
    weather = {"source": "BOM Hourly API", "headwind_kmh": 0.0, "wind_speed_kmh": 5.0, "rain_expected_mm": 0.0, "effect": "Calm"}

    r1 = _sample_race(field_size=8)
    r1["race_number"] = 1
    r1["scored_runners"] = score_race_23pt(r1, track_meta, weather)
    r1["results"] = [{"position": 1, "runner_number": 1, "barrier": 1, "name": "Runner 1"}]

    r2 = _sample_race(field_size=8)
    r2["race_number"] = 2
    r2["scored_runners"] = score_race_23pt(r2, track_meta, weather)
    r2["results"] = [{"position": 1, "runner_number": 2, "barrier": 2, "name": "Runner 2"}]

    r3 = _sample_race(field_size=8)
    r3["race_number"] = 3
    r3["scored_runners"] = score_race_23pt(r3, track_meta, weather)
    r3["results"] = [{"position": 1, "runner_number": 8, "barrier": 8, "name": "Runner 8"}]

    # Race 1 must ignore R1, R2, R3 -> stays neutral 5.0 (sample_size=0)
    b1 = apply_progressive_meeting_bias(r1, [r1, r2, r3])
    assert b1["sample_size"] == 0
    f4_r1 = next(a for a in r1["scored_runners"][0]["audit_23pt"] if a["key"] == "current_meeting_bias")
    assert f4_r1["score"] == NEUTRAL_SCORE
    assert f4_r1["has_data"] is False

    # Race 2 must ONLY use Race 1 (sample_size=1)
    b2 = apply_progressive_meeting_bias(r2, [r1, r2, r3])
    assert b2["sample_size"] == 1

    # Race 3 must ONLY use Race 1 & Race 2 (sample_size=2, never Race 3's own result!)
    b3 = apply_progressive_meeting_bias(r3, [r1, r2, r3])
    assert b3["sample_size"] == 2
    assert b3["pace_bias"] == "On-Pace"
    assert b3["barrier_bias"] == "Inside"
    f4_r3_onpace = next(
        a for r in r3["scored_runners"] if r["runner_number"] == 1
        for a in r["audit_23pt"] if a["key"] == "current_meeting_bias"
    )
    assert f4_r3_onpace["has_data"] is True
    assert f4_r3_onpace["score"] > 5.0
