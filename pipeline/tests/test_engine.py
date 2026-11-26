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
    assert len(scored[0]["audit_23pt"]) == 24

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


def test_nsw_freefields_parsers_and_general_youtube_matching():
    from pipeline.src.engine.scorer_23pt import attach_enrichment_to_runners
    from pipeline.src.sources.nsw_freefields import (
        parse_ladbrokes_blog_notes,
        parse_pre_stewards_html,
        parse_trial_results_html,
    )
    from pipeline.src.sources.youtube_feed import count_runner_mentions, match_general_preview_videos

    pre_stew_html = """
    <table>
      <tr><td>1</td><td>RUNNER 1</td><td>14/11/2026</td><td>Gosford</td><td>Held up at the 300m and raced wide without cover.</td></tr>
      <tr><td>2</td><td>RUNNER 2</td><td>10/11/2026</td><td>Wyong</td><td>Post-race vet exam revealed lame in near foreleg; veterinary clearance required.</td></tr>
      <tr><td>3</td><td>RUNNER 3</td><td>26/11/2026</td><td>Hawkesbury</td><td>Post-race incident on target date should be filtered by cutoff.</td></tr>
    </table>
    """
    stew_map = parse_pre_stewards_html(pre_stew_html, cutoff_date_str="2026-11-26")
    assert "runner1" in stew_map and len(stew_map["runner1"]["stewards_notes"]) == 1
    assert "runner2" in stew_map and len(stew_map["runner2"]["vet_notes"]) == 1
    assert "runner3" not in stew_map  # Strictly filtered out by cutoff date

    trial_html = """
    <table>
      <tr><td></td><td>1</td><td>4</td><td>RUNNER 1</td><td>Chris Waller</td><td>James McDonald</td><td>0</td><td>0</td></tr>
      <tr><td></td><td>2</td><td>6</td><td>RUNNER 2</td><td>Ciaron Maher</td><td>Tyler Schiller</td><td>1.25</td><td>0</td></tr>
    </table>
    """
    trials_map = parse_trial_results_html(trial_html, venue="Warwick Farm", trial_date_str="2026-11-24")
    assert trials_map["runner1"][0]["position"] == 1
    assert trials_map["runner2"][0]["margin"] == 1.25

    blog_html = "<p>Runner 1 trialled up sharply at Warwick Farm and looks ready to fire first-up.</p>"
    blog_map = parse_ladbrokes_blog_notes(blog_html, ["Runner 1", "Runner 2"])
    assert "runner1" in blog_map and "runner2" not in blog_map

    # Attach to sample race and confirm recent_trials, stewards_comments, and injury_history flip LIVE
    race = _sample_race(field_size=8)
    attach_enrichment_to_runners(
        race["runners"],
        trials_by_horse=trials_map,
        pre_stewards_by_horse=stew_map,
        blog_notes_by_horse=blog_map,
        nsw_freefields_checked=True,
    )
    track_meta = {"name": "Royal Randwick", "home_straight_m": 410, "turning": "Clockwise"}
    weather = {"source": "BOM Hourly API", "headwind_kmh": 0.0, "wind_speed_kmh": 5.0, "rain_expected_mm": 0.0, "effect": "Calm"}
    scored = score_race_23pt(race, track_meta, weather)
    r1 = next(r for r in scored if r["runner_number"] == 1)
    r2 = next(r for r in scored if r["runner_number"] == 2)
    r1_aud = {a["key"]: a for a in r1["audit_23pt"]}
    r2_aud = {a["key"]: a for a in r2["audit_23pt"]}

    assert r1_aud["recent_trials"]["has_data"] is True and r1_aud["recent_trials"]["score"] > 7.5
    assert r1_aud["stewards_comments"]["has_data"] is True and r1_aud["stewards_comments"]["score"] > 6.5
    assert r1_aud["injury_history"]["has_data"] is True and r1_aud["injury_history"]["score"] == NEUTRAL_SCORE
    assert r2_aud["injury_history"]["has_data"] is True and r2_aud["injury_history"]["score"] < 4.0

    # General YouTube preview matching
    vids = [{"video_id": "g1", "title": "Thursday Best Bets & Racing Preview", "channel": "Punters"}]
    cands = match_general_preview_videos(vids, ["best bets", "racing preview"])
    assert len(cands) == 1
    assert count_runner_mentions("We love Stonecrest in R1 and Outta Line in R2.", ["Stonecrest", "Outta Line", "Other"]) == 2


def test_youtube_media_description_fallback_when_captions_ip_blocked(monkeypatch):
    from datetime import datetime, timezone
    from pipeline.src.engine.gemini_batch import GeminiBatchAnalyzer
    from pipeline.src.http_client import PoliteHttpClient
    from pipeline.src.sources import youtube_feed
    from pipeline.src.sources.youtube_feed import YouTubeFeedDiscovery, parse_youtube_atom_feed

    atom_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
          xmlns:media="http://search.yahoo.com/mrss/"
          xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <yt:videoId>vidDescGeel</yt:videoId>
        <title>Midweek Best Bets &amp; Form Preview (25/11)</title>
        <published>2026-11-24T21:00:00+00:00</published>
        <media:group>
          <media:description>
            00:00 Intro
            02:15 Geelong R1 - Runner 1 looks super sharp off a recent trial.
            05:30 Geelong R2 - Runner 2 gets the perfect gun run from barrier 2.
            Subscribe to our channel: https://youtube.com/example
            Instagram: https://instagram.com/example
          </media:description>
        </media:group>
      </entry>
      <entry>
        <yt:videoId>vidPostRace</yt:videoId>
        <title>Geelong Post-Race Wrap &amp; Winners</title>
        <published>2026-11-25T07:30:00+00:00</published>
        <media:group>
          <media:description>Runner 1 won easily at Geelong.</media:description>
        </media:group>
      </entry>
    </feed>"""

    cutoff = datetime(2026, 11, 25, 1, 0, 0, tzinfo=timezone.utc)
    recent = parse_youtube_atom_feed(atom_xml, "Racenet", lookback_hours=60, upper_bound_utc=cutoff)
    assert len(recent) == 1
    assert recent[0]["video_id"] == "vidDescGeel"
    assert "Runner 1 looks super sharp" in recent[0]["description"]
    assert "Subscribe to our channel" not in recent[0]["description"]

    # Simulate Azure / GitHub Actions YouTube caption IP block
    def _raise_ip_block(urls, max_chars_per_video=8000):
        raise RuntimeError("YouTube is blocking requests from your IP (cloud provider)")

    monkeypatch.setattr(youtube_feed, "fetch_transcripts_for_urls", _raise_ip_block)

    disc = YouTubeFeedDiscovery(PoliteHttpClient())
    # 1) Venue match in <media:description> succeeds despite IP block
    urls, text = disc.fetch_meeting_transcript(
        recent_videos=recent,
        meeting_name="Geelong",
        aliases=["Ladbrokes Geelong"],
        runner_names=["Runner 1", "Runner 2", "Runner 3"],
    )
    assert urls == ["https://www.youtube.com/watch?v=vidDescGeel"]
    assert "Runner 1 looks super sharp" in text

    # 2) Runner-mention match (>=2 runners in <media:description>) succeeds even when venue name differs
    urls_gen, text_gen = disc.fetch_meeting_transcript(
        recent_videos=recent,
        meeting_name="Hawkesbury",
        aliases=[],
        runner_names=["Runner 1", "Runner 2", "Runner 3"],
    )
    assert urls_gen == ["https://www.youtube.com/watch?v=vidDescGeel"]
    assert "Runner 2 gets the perfect gun run" in text_gen

    # 3) #23 youtube_transcripts flips LIVE for mentioned runners & stays anti-hallucination locked at 5.0 for unmentioned
    race = _sample_race(field_size=8)
    track_meta = {"name": "Geelong", "home_straight_m": 350, "turning": "Anti-clockwise"}
    weather = {"source": "BOM Hourly API", "headwind_kmh": 0.0, "wind_speed_kmh": 5.0, "rain_expected_mm": 0.0, "effect": "Calm"}
    race["scored_runners"] = score_race_23pt(race, track_meta, weather, youtube_transcript=text)

    r1 = next(r for r in race["scored_runners"] if r["runner_number"] == 1)
    r3 = next(r for r in race["scored_runners"] if r["runner_number"] == 3)
    f23_r1 = next(a for a in r1["audit_23pt"] if a["key"] == "youtube_transcripts")
    f23_r3 = next(a for a in r3["audit_23pt"] if a["key"] == "youtube_transcripts")
    assert f23_r1["has_data"] is True and f23_r1["score"] == 6.4
    assert f23_r3["has_data"] is True and f23_r3["score"] == NEUTRAL_SCORE

    # Gemini must NOT be able to override unmentioned runner #3 away from 5.0, while runner #1 can be refined
    analyzer = GeminiBatchAnalyzer(api_key="")
    ai_race = {
        "race_id": race["race_id"],
        "runners": [
            {"runner_number": 1, "factor_overrides": {"youtube_transcripts": {"score": 8.0, "reason": "Praised in YouTube preview."}}},
            {"runner_number": 3, "factor_overrides": {"youtube_transcripts": {"score": 9.0, "reason": "Hallucinated praise."}}},
        ],
    }
    analyzer._apply_validated_overrides(race, ai_race, youtube_transcript=text)
    f23_r1_after = next(a for r in race["scored_runners"] if r["runner_number"] == 1 for a in r["audit_23pt"] if a["key"] == "youtube_transcripts")
    f23_r3_after = next(a for r in race["scored_runners"] if r["runner_number"] == 3 for a in r["audit_23pt"] if a["key"] == "youtube_transcripts")
    assert f23_r1_after["score"] > 7.0
    assert f23_r3_after["score"] == NEUTRAL_SCORE


def test_meeting_bias_pace_style_normalization_and_progressive_shifts():
    from pipeline.src.engine.scorer_23pt import (
        apply_progressive_meeting_bias,
        classify_runner_pace_style,
        compute_meeting_bias,
    )

    # Verify all 6 Ladbrokes labels + nested speedmap + numeric settling_position
    assert classify_runner_pace_style({"speedmap_label": "Leader"}) == "on_pace"
    assert classify_runner_pace_style({"speedmap_label": "Pace"}) == "on_pace"
    assert classify_runner_pace_style({"speedmap_label": "Off Pace"}) == "on_pace"
    assert classify_runner_pace_style({"speedmap_label": "Midfield"}) == "neutral"
    assert classify_runner_pace_style({"speedmap_label": "Off Midfield"}) == "closer"
    assert classify_runner_pace_style({"speedmap_label": "Backmarker"}) == "closer"
    assert classify_runner_pace_style({"speedmap": {"label": "Off-Midfield"}}) == "closer"
    assert classify_runner_pace_style({"pace_style": "On-Pace"}) == "on_pace"
    assert classify_runner_pace_style({"settling_position": 2}) == "on_pace"
    assert classify_runner_pace_style({"settling_position": 7}) == "closer"
    assert classify_runner_pace_style({"settling_position": 5}) == "neutral"

    # Build 5 Firestore-style settled races (using `scored_runners` only, NO `runners` key!)
    def _mk_settled_race(rnum: int, win_label: str, win_bar: int, mode: str = "results"):
        runners = [
            {"runner_number": i, "barrier": i, "name": f"H{rnum}_{i}", "speedmap_label": win_label if i == 1 else "Midfield", "is_scratched": False}
            for i in range(1, 10)
        ]
        runners[0]["barrier"] = win_bar
        doc = {"race_number": rnum, "scored_runners": runners}
        if mode == "results":
            doc["results"] = [{"position": 1, "runner_number": 1, "barrier": win_bar, "name": f"H{rnum}_1"}]
        elif mode == "official_top4":
            doc["official_top4"] = [1, 2, 3, 4]
        elif mode == "finishing_position":
            runners[0]["finishing_position"] = 1
        return doc

    r1 = _mk_settled_race(1, "Leader", 1, mode="results")
    r2 = _mk_settled_race(2, "Off Pace", 2, mode="official_top4")
    r3 = _mk_settled_race(3, "Midfield", 5, mode="finishing_position")
    r4 = _mk_settled_race(4, "Off Midfield", 8, mode="results")
    r5 = _mk_settled_race(5, "Backmarker", 9, mode="results")

    mixed_bias = compute_meeting_bias([r1, r2, r3, r4, r5])
    assert mixed_bias["sample_size"] == 5
    assert mixed_bias["on_pace_wins"] == 2
    assert mixed_bias["closer_wins"] == 2
    assert mixed_bias["neutral_wins"] == 1
    assert "(2 on-pace / 2 closers)" in mixed_bias["summary"]
    assert "(2 inside / 2 wide)" in mixed_bias["summary"]

    # After 2 on-pace inside winners (Leader + Off Pace), R3 must detect On-Pace + Inside bias
    on_pace_bias = compute_meeting_bias([r1, r2])
    assert on_pace_bias["pace_bias"] == "On-Pace"
    assert on_pace_bias["barrier_bias"] == "Inside"
    assert "(2 on-pace / 0 closers)" in on_pace_bias["summary"]

    # Verify progressive Factor #4 score shifts on R3 runners (Off Pace boosted, Off Midfield penalized)
    track_meta = {"name": "Hawkesbury", "home_straight_m": 380, "turning": "Clockwise"}
    weather = {"source": "BOM Hourly API", "headwind_kmh": 0.0, "wind_speed_kmh": 5.0, "rain_expected_mm": 0.0, "effect": "Calm"}
    next_race = _sample_race(field_size=8)
    next_race["race_number"] = 3
    next_race["runners"][0]["speedmap_label"] = "Off Pace"
    next_race["runners"][0]["barrier"] = 1
    next_race["runners"][1]["speedmap_label"] = "Off Midfield"
    next_race["runners"][1]["barrier"] = 7
    next_race["runners"][2]["speedmap_label"] = "Midfield"
    next_race["runners"][2]["barrier"] = 5

    next_race["scored_runners"] = score_race_23pt(next_race, track_meta, weather, meeting_bias=on_pace_bias)
    f4_off_pace = next(a for r in next_race["scored_runners"] if r["runner_number"] == 1 for a in r["audit_23pt"] if a["key"] == "current_meeting_bias")
    f4_off_mid = next(a for r in next_race["scored_runners"] if r["runner_number"] == 2 for a in r["audit_23pt"] if a["key"] == "current_meeting_bias")
    f4_mid = next(a for r in next_race["scored_runners"] if r["runner_number"] == 3 for a in r["audit_23pt"] if a["key"] == "current_meeting_bias")
    assert f4_off_pace["has_data"] is True and f4_off_pace["score"] == 8.0  # +2.0 On-Pace +1.0 Inside
    assert f4_off_mid["has_data"] is True and f4_off_mid["score"] == 3.6   # -1.4 Closer penalty
    assert f4_mid["has_data"] is True and f4_mid["score"] == 5.0           # Neutral Midfield

    # Verify Swoopers bias (Off Midfield + Backmarker) boosts Off Midfield and penalizes Leader
    r_sw1 = _mk_settled_race(1, "Off Midfield", 8)
    r_sw2 = _mk_settled_race(2, "Backmarker", 9)
    next_race["runners"][0]["speedmap_label"] = "Leader"
    next_race["scored_runners"] = score_race_23pt(next_race, track_meta, weather)
    sw_bias = apply_progressive_meeting_bias(next_race, [r_sw1, r_sw2])
    assert sw_bias["pace_bias"] == "Swoopers"
    assert sw_bias["closer_wins"] == 2
    f4_lead_sw = next(a for r in next_race["scored_runners"] if r["runner_number"] == 1 for a in r["audit_23pt"] if a["key"] == "current_meeting_bias")
    f4_close_sw = next(a for r in next_race["scored_runners"] if r["runner_number"] == 2 for a in r["audit_23pt"] if a["key"] == "current_meeting_bias")
    assert f4_close_sw["score"] == 8.0  # +2.0 Swoopers +1.0 Outside
    assert f4_lead_sw["score"] == 3.6   # -1.4 Leader penalty


def test_srm_min_odds_gate_picnic_margin_bayesian_and_two_tier_outsider_ceiling():
    from pipeline.src.engine.pricing import MIN_SRM_ODDS
    from pipeline.src.engine.scorer_23pt import bayesian_strike_rate

    # 1) Bayesian shrinkage: 3/10 (30% raw) -> 17.1%, while 23/124 (18.5% raw) -> 17.4% (higher!)
    sr_small = bayesian_strike_rate(3, 10)
    sr_estab = bayesian_strike_rate(23, 124)
    assert abs(sr_small - (6.0 / 35.0)) < 1e-6
    assert abs(sr_estab - (26.0 / 149.0)) < 1e-6
    assert sr_estab > sr_small

    # 2) Structured last_twenty_starts picnic / beaten-margin / small-field last-place penalties
    track_meta = {"name": "Mornington", "home_straight_m": 250, "turning": "Anti-clockwise"}
    weather = {"source": "BOM Hourly API", "headwind_kmh": 0.0, "wind_speed_kmh": 5.0, "rain_expected_mm": 0.0, "effect": "Calm"}
    race = _sample_race(field_size=8)
    # Runner 4 mimics Fulhamzedge: Balranald picnic tail-off (39.92L, 6th of 6, $7,000 purse) + small-sample 3/10 trainer
    race["runners"][3]["last_twenty_starts"] = [
        {"date": "2026-11-18", "venue": "Balranald", "position": 6, "field_size": 6, "margin": 39.92, "prize_money": 7000},
        {"date": "2026-11-05", "venue": "Balranald Picnic", "position": 2, "field_size": 5, "margin": 3.5, "prize_money": 6000},
    ]
    race["runners"][3]["jockey_stats"] = {
        "last_50_starts": {"number_of_starts": 50, "number_of_wins": 7, "number_of_seconds": 6, "number_of_thirds": 8},
        "trainer": {"number_of_starts": 10, "number_of_wins": 3, "number_of_seconds": 0, "number_of_thirds": 0},
    }
    # Runner 8 mimics established yard 23/124
    race["runners"][7]["jockey_stats"] = {
        "last_50_starts": {"number_of_starts": 50, "number_of_wins": 9, "number_of_seconds": 4, "number_of_thirds": 7},
        "trainer": {"number_of_starts": 124, "number_of_wins": 23, "number_of_seconds": 9, "number_of_thirds": 10},
    }

    scored = score_race_23pt(race, track_meta, weather)
    r4 = next(r for r in scored if r["runner_number"] == 4)
    r8 = next(r for r in scored if r["runner_number"] == 8)
    r4_aud = {a["key"]: a for a in r4["audit_23pt"]}
    r8_aud = {a["key"]: a for a in r8["audit_23pt"]}

    assert r4_aud["adjusted_sectionals"]["score"] < NEUTRAL_SCORE
    assert r4_aud["class_track_distance"]["score"] < NEUTRAL_SCORE
    assert "39.9L" in r4_aud["adjusted_sectionals"]["reason"]
    assert "17.1% Bayes SR" in r4_aud["trainer_form"]["reason"]
    assert r8_aud["trainer_form"]["score"] >= r4_aud["trainer_form"]["score"]

    # 3) Two-tier $31+ market ceiling: Tier 1 ($61 non-firmer) capped at 2.5x; Tier 2 ($91 sharp firmer) allowed up to 8.5x with >=$10 floor
    pricing_race = {
        "race_id": "race-ceiling-test",
        "scored_runners": [
            {"runner_number": 1, "name": "Fav One", "barrier": 1, "jockey": "J1", "trainer": "T1", "composite_score": 59.0, "opening_win": 3.0, "live_win": 2.4, "market_fluc": {"direction": "Firmer", "fluc_pct": 25.0}},
            {"runner_number": 2, "name": "Sec Fav", "barrier": 2, "jockey": "J2", "trainer": "T2", "composite_score": 56.5, "opening_win": 3.5, "live_win": 3.0, "market_fluc": {"direction": "Firmer", "fluc_pct": 16.7}},
            {"runner_number": 3, "name": "Mid Chance", "barrier": 3, "jockey": "J3", "trainer": "T3", "composite_score": 54.0, "opening_win": 7.0, "live_win": 7.0, "market_fluc": {"direction": "Steady", "fluc_pct": 0.0}},
            {"runner_number": 5, "name": "Fourth Chance", "barrier": 5, "jockey": "J5", "trainer": "T5", "composite_score": 53.0, "opening_win": 10.0, "live_win": 10.0, "market_fluc": {"direction": "Steady", "fluc_pct": 0.0}},
            {"runner_number": 4, "name": "Fulhamzedge", "barrier": 4, "jockey": "J4", "trainer": "T4", "composite_score": 58.0, "opening_win": 67.0, "live_win": 61.0, "market_fluc": {"direction": "Firmer", "fluc_pct": 9.8}},
            {"runner_number": 8, "name": "Just Jean", "barrier": 8, "jockey": "J8", "trainer": "T8", "composite_score": 57.6, "opening_win": 126.0, "live_win": 91.0, "market_fluc": {"direction": "Firmer", "fluc_pct": 38.5}},
            {"runner_number": 6, "name": "No Hope", "barrier": 6, "jockey": "J6", "trainer": "T6", "composite_score": 48.0, "opening_win": 51.0, "live_win": 51.0, "market_fluc": {"direction": "Steady", "fluc_pct": 0.0}},
        ],
    }
    priced = price_and_generate_bets(pricing_race)
    top_by_num = {r["runner_number"]: r for r in pricing_race["scored_runners"]}
    # Tier 1 $61 non-firmer (#4 Fulhamzedge) must drop out of Top 4
    assert top_by_num[4]["rank"] > 4
    assert top_by_num[4]["rated_price"] > 20.0
    # Tier 2 $91 sharp firmer (#8 Just Jean) gets 8.5x allowance (>= $10.00 Rated Price floor) & beats Tier 1 outsider
    assert top_by_num[8]["rated_price"] >= 10.00
    assert top_by_num[8]["probabilities"]["win"] > top_by_num[4]["probabilities"]["win"]

    # 4) $1.80 SRM minimum odds gate (e.g. Hawkesbury R1 $1.25 2-Leg SRM)
    low_srm_race = {
        "race_id": "race-low-srm",
        "date": "2026-11-26",
        "meeting_slug": "hawkesbury",
        "meeting_name": "Hawkesbury",
        "race_number": 1,
        "name": "Low Odds SRM Test",
        "scored_runners": [
            {"runner_number": 5, "name": "Odds On A", "barrier": 1, "jockey": "J1", "trainer": "T1", "composite_score": 76.0, "opening_win": 1.35, "live_win": 1.25, "market_fluc": {"direction": "Firmer", "fluc_pct": 8.0}},
            {"runner_number": 4, "name": "Odds On B", "barrier": 2, "jockey": "J2", "trainer": "T2", "composite_score": 71.0, "opening_win": 2.40, "live_win": 2.20, "market_fluc": {"direction": "Firmer", "fluc_pct": 9.1}},
            {"runner_number": 6, "name": "Third Fav", "barrier": 3, "jockey": "J3", "trainer": "T3", "composite_score": 55.0, "opening_win": 9.0, "live_win": 10.0, "market_fluc": {"direction": "Drifter", "fluc_pct": -10.0}},
            {"runner_number": 1, "name": "Fourth", "barrier": 4, "jockey": "J4", "trainer": "T4", "composite_score": 50.0, "opening_win": 15.0, "live_win": 18.0, "market_fluc": {"direction": "Drifter", "fluc_pct": -16.7}},
            {"runner_number": 2, "name": "Fifth", "barrier": 5, "jockey": "J5", "trainer": "T5", "composite_score": 44.0, "opening_win": 31.0, "live_win": 41.0, "market_fluc": {"direction": "Drifter", "fluc_pct": -24.4}},
        ],
    }
    p_low = price_and_generate_bets(low_srm_race)
    srm2 = next(s for s in p_low["srm_combos"] if s["leg_count"] == 2)
    assert srm2["estimated_odds"] < MIN_SRM_ODDS
    assert srm2["skipped"] is True
    assert srm2["unit_stake"] == 0.0
    assert srm2["skip_reason"] == f"Skipped: Est. odds ${srm2['estimated_odds']:.2f} < $1.80 min (low payout)"

    # Excluded from settle_race so it is never staked or counted as a loss in Tab 2
    low_srm_race.update(p_low)
    results = [
        {"position": 1, "runner_number": 1, "name": "Fourth", "barrier": 4, "margin_length": 0.0},
        {"position": 2, "runner_number": 2, "name": "Fifth", "barrier": 5, "margin_length": 1.0},
        {"position": 3, "runner_number": 6, "name": "Third Fav", "barrier": 3, "margin_length": 2.0},
        {"position": 4, "runner_number": 4, "name": "Odds On B", "barrier": 2, "margin_length": 3.0},
        {"position": 5, "runner_number": 5, "name": "Odds On A", "barrier": 1, "margin_length": 4.0},
    ]
    settled = settle_race(low_srm_race, results, {"best_tote_win": {"1": 18.0}})
    settled_srm_legs = {s["leg_count"] for s in settled["srm"]}
    assert 2 not in settled_srm_legs


def test_24pt_weights_jockey_split_and_srm_field_size_precedence():
    from pipeline.src.engine.scorer_24pt import load_factor_weights, score_race_24pt

    # 1) Verify exact 24 factors summing to 100.0%
    cfg = load_factor_weights()
    factors = cfg["factors"]
    assert len(factors) == 24
    total_w = round(sum(float(f["weight"]) for f in factors), 4)
    assert total_w == 100.0
    w_map = {f["key"]: float(f["weight"]) for f in factors}
    assert w_map["jockey_horse_affinity"] == 2.5
    assert w_map["jockey_course_record"] == 2.5
    assert "jockey_track_familiarity" not in w_map

    # 2) Verify Jockey-Horse Affinity (k=6) vs Bayesian Jockey Course Record (k=25)
    track_meta = {"name": "Mornington", "home_straight_m": 250, "turning": "Anti-clockwise", "aliases": ["Mornington"]}
    weather = {"source": "BOM Hourly API", "headwind_kmh": 0.0, "wind_speed_kmh": 5.0, "rain_expected_mm": 0.0, "effect": "Calm"}
    race = _sample_race(field_size=6)
    race["meeting_name"] = "Mornington"
    # Runner 1: 0 prior jockey-horse rides -> neutral 5.0; established course specialist 18W/75 at Mornington
    race["runners"][0]["past_performance_stats"] = {"jockey": {"number_of_starts": 0, "number_of_wins": 0}}
    race["runners"][0]["jockey_stats"] = {"track": {"number_of_starts": 75, "number_of_wins": 18, "number_of_seconds": 10, "number_of_thirds": 8}}
    # Runner 2: 3W/6 rides on this horse (strong affinity); low-sample course record 1W/2 (50% raw -> 14.8% Bayes SR)
    race["runners"][1]["jockey"] = "Apprentice Two"
    race["runners"][1]["past_performance_stats"] = {"jockey": {"number_of_starts": 6, "number_of_wins": 3, "number_of_seconds": 1, "number_of_thirds": 0}}
    race["runners"][1]["jockey_stats"] = {"track": {"number_of_starts": 2, "number_of_wins": 1, "number_of_seconds": 0, "number_of_thirds": 0}}
    # Runner 3: 1W/1 ride on this horse (100% raw -> 24.6% Bayes SR, moderated below 3W/6 = 31.0% Bayes SR!)
    race["runners"][2]["jockey"] = "Jockey Three"
    race["runners"][2]["past_performance_stats"] = {"jockey": {"number_of_starts": 1, "number_of_wins": 1, "number_of_seconds": 0, "number_of_thirds": 0}}
    race["runners"][2]["jockey_stats"] = {}

    scored = score_race_24pt(race, track_meta, weather)
    aud = {r["runner_number"]: {a["key"]: a for a in r["audit_23pt"]} for r in scored}

    # Jockey-Horse Affinity checks
    assert aud[1]["jockey_horse_affinity"]["score"] == NEUTRAL_SCORE
    assert aud[1]["jockey_horse_affinity"]["has_data"] is False
    assert aud[2]["jockey_horse_affinity"]["has_data"] is True
    assert aud[3]["jockey_horse_affinity"]["has_data"] is True
    assert aud[2]["jockey_horse_affinity"]["score"] > aud[3]["jockey_horse_affinity"]["score"]

    # Jockey Course Record checks: established 18/75 (21.0% Bayes SR) MUST beat low-sample 1/2 (14.8% Bayes SR)
    assert aud[1]["jockey_course_record"]["has_data"] is True
    assert aud[2]["jockey_course_record"]["has_data"] is True
    assert "14.8% Bayes SR" in aud[2]["jockey_course_record"]["reason"]
    assert aud[1]["jockey_course_record"]["score"] > aud[2]["jockey_course_record"]["score"]
    assert aud[3]["jockey_course_record"]["score"] == NEUTRAL_SCORE
    assert aud[3]["jockey_course_record"]["has_data"] is False

    # 3) SRM Field-Size Skip Precedence strictly BEFORE $1.80 minimum odds gate
    # Case A: 4-runner field with ultra-short odds (< $1.80) -> ALL SRMs must show MIN FIELD 5 (not LOW ODDS!)
    four_runner_race = {
        "race_id": "race-4-runners",
        "date": "2026-11-27",
        "meeting_slug": "flemington",
        "meeting_name": "Flemington",
        "race_number": 1,
        "name": "Small 4-Runner Sprint",
        "scored_runners": [
            {"runner_number": 1, "name": "Short A", "barrier": 1, "jockey": "J1", "trainer": "T1", "composite_score": 76.0, "opening_win": 1.3, "live_win": 1.2, "market_fluc": {"direction": "Firmer", "fluc_pct": 8.3}},
            {"runner_number": 2, "name": "Short B", "barrier": 2, "jockey": "J2", "trainer": "T2", "composite_score": 70.0, "opening_win": 2.4, "live_win": 2.2, "market_fluc": {"direction": "Firmer", "fluc_pct": 9.1}},
            {"runner_number": 3, "name": "Mid C", "barrier": 3, "jockey": "J3", "trainer": "T3", "composite_score": 55.0, "opening_win": 10.0, "live_win": 12.0, "market_fluc": {"direction": "Drifter", "fluc_pct": -16.7}},
            {"runner_number": 4, "name": "Long D", "barrier": 4, "jockey": "J4", "trainer": "T4", "composite_score": 48.0, "opening_win": 21.0, "live_win": 31.0, "market_fluc": {"direction": "Drifter", "fluc_pct": -32.3}},
        ],
    }
    p4 = price_and_generate_bets(four_runner_race)
    for combo in p4["srm_combos"]:
        assert combo["skipped"] is True
        assert combo["unit_stake"] == 0.0
        assert combo["skip_reason"] == "Skipped: Field size < 5 min for SRM"
        assert combo["skip_badge"] == "SKIP · MIN FIELD 5"

    # Case B: 5-runner field -> 2-Leg evaluated by $1.80 gate, while 3-Leg & 4-Leg get MIN FIELD 6 (even if < $1.80!)
    five_runner_race = {
        "race_id": "race-5-runners",
        "date": "2026-11-27",
        "meeting_slug": "flemington",
        "meeting_name": "Flemington",
        "race_number": 2,
        "name": "5-Runner Handicap",
        "scored_runners": [
            {"runner_number": 1, "name": "Even A", "barrier": 1, "jockey": "J1", "trainer": "T1", "composite_score": 60.0, "opening_win": 3.5, "live_win": 3.5, "market_fluc": {"direction": "Steady", "fluc_pct": 0.0}},
            {"runner_number": 2, "name": "Even B", "barrier": 2, "jockey": "J2", "trainer": "T2", "composite_score": 58.0, "opening_win": 4.0, "live_win": 4.0, "market_fluc": {"direction": "Steady", "fluc_pct": 0.0}},
            {"runner_number": 3, "name": "Even C", "barrier": 3, "jockey": "J3", "trainer": "T3", "composite_score": 56.0, "opening_win": 5.0, "live_win": 5.0, "market_fluc": {"direction": "Steady", "fluc_pct": 0.0}},
            {"runner_number": 4, "name": "Even D", "barrier": 4, "jockey": "J4", "trainer": "T4", "composite_score": 54.0, "opening_win": 6.0, "live_win": 6.0, "market_fluc": {"direction": "Steady", "fluc_pct": 0.0}},
            {"runner_number": 5, "name": "Even E", "barrier": 5, "jockey": "J5", "trainer": "T5", "composite_score": 52.0, "opening_win": 7.0, "live_win": 7.0, "market_fluc": {"direction": "Steady", "fluc_pct": 0.0}},
        ],
    }
    p5 = price_and_generate_bets(five_runner_race)
    s5_map = {c["leg_count"]: c for c in p5["srm_combos"]}
    # 3-Leg & 4-Leg must ALWAYS be skipped with MIN FIELD 6 in a 5-runner field (even when est_odds < $1.80)
    assert s5_map[3]["skipped"] is True
    assert s5_map[3]["skip_reason"] == "Skipped: Top 4 SRM requires 6+ runners"
    assert s5_map[3]["skip_badge"] == "SKIP · MIN FIELD 6"
    assert s5_map[4]["skipped"] is True
    assert s5_map[4]["skip_reason"] == "Skipped: Top 4 SRM requires 6+ runners"
    assert s5_map[4]["skip_badge"] == "SKIP · MIN FIELD 6"
    # 2-Leg in a 5-runner field passes Steps 1-2 and is judged only by Step 3 ($1.80 gate)
    if s5_map[2]["estimated_odds"] < 1.80:
        assert s5_map[2]["skip_badge"] == "SKIP · LOW ODDS"
    else:
        assert s5_map[2]["skipped"] is False

    # Confirm settle_race excludes all field-gated SRMs
    five_runner_race.update(p5)
    results5 = [
        {"position": i, "runner_number": i, "name": f"Even {i}", "barrier": i, "margin_length": i * 0.5}
        for i in range(1, 6)
    ]
    settled5 = settle_race(five_runner_race, results5, {"best_tote_win": {"1": 3.5}})
    settled5_legs = {s["leg_count"] for s in settled5["srm"]}
    assert 3 not in settled5_legs and 4 not in settled5_legs


def test_relative_tier2_maiden_threshold_and_tiered_exotic_return_floors():
    from pipeline.src.engine.pricing import MIN_EXOTIC_RETURN, _build_single_exotic

    # 1) Relative-to-field Tier-2 threshold in a compressed maiden field (median ~52.5 -> threshold max(53.5, 53.5) = 53.5)
    maiden_race = {
        "race_id": "maiden-compressed",
        "scored_runners": [
            {"runner_number": 1, "name": "Fav A", "barrier": 1, "jockey": "J1", "trainer": "T1", "composite_score": 55.8, "opening_win": 3.0, "live_win": 2.6, "market_fluc": {"direction": "Firmer", "fluc_pct": 15.4}},
            {"runner_number": 2, "name": "Sec B", "barrier": 2, "jockey": "J2", "trainer": "T2", "composite_score": 53.2, "opening_win": 3.5, "live_win": 3.2, "market_fluc": {"direction": "Firmer", "fluc_pct": 9.4}},
            {"runner_number": 3, "name": "Mid C", "barrier": 3, "jockey": "J3", "trainer": "T3", "composite_score": 52.6, "opening_win": 6.0, "live_win": 6.5, "market_fluc": {"direction": "Drifter", "fluc_pct": -7.7}},
            {"runner_number": 4, "name": "Mid D", "barrier": 4, "jockey": "J4", "trainer": "T4", "composite_score": 52.4, "opening_win": 9.0, "live_win": 10.0, "market_fluc": {"direction": "Drifter", "fluc_pct": -10.0}},
            # Sharp $91 firmer with 54.2 composite (< old 55.0 cliff, but >= max(53.5, 52.5+1.0) = 53.5!) -> qualifies for Tier 2 (8.5x, >=$10 floor)
            {"runner_number": 8, "name": "Maiden Firmer", "barrier": 8, "jockey": "J8", "trainer": "T8", "composite_score": 54.2, "opening_win": 126.0, "live_win": 91.0, "market_fluc": {"direction": "Firmer", "fluc_pct": 38.5}},
            # Non-firming $61 outsider with same 54.2 composite (fluc +8.0% < +25%) -> stays capped at Tier 1 (2.5x)
            {"runner_number": 6, "name": "Maiden NonFirmer", "barrier": 6, "jockey": "J6", "trainer": "T6", "composite_score": 54.2, "opening_win": 67.0, "live_win": 61.0, "market_fluc": {"direction": "Firmer", "fluc_pct": 9.8}},
            # Below-threshold $91 firmer (53.0 < 53.5) -> stays capped at Tier 1 (2.5x)
            {"runner_number": 7, "name": "Weak Firmer", "barrier": 7, "jockey": "J7", "trainer": "T7", "composite_score": 53.0, "opening_win": 126.0, "live_win": 91.0, "market_fluc": {"direction": "Firmer", "fluc_pct": 38.5}},
            {"runner_number": 5, "name": "Tail E", "barrier": 5, "jockey": "J5", "trainer": "T5", "composite_score": 50.5, "opening_win": 21.0, "live_win": 26.0, "market_fluc": {"direction": "Drifter", "fluc_pct": -19.2}},
        ],
    }
    price_and_generate_bets(maiden_race)
    by_num = {r["runner_number"]: r for r in maiden_race["scored_runners"]}
    # Runner 8 qualified for Tier 2 (8.5x allowance) despite composite 54.2 < 55.0
    assert by_num[8]["rated_price"] >= 10.00
    assert by_num[8]["probabilities"]["win"] > by_num[6]["probabilities"]["win"] * 1.5
    assert by_num[8]["probabilities"]["win"] > by_num[7]["probabilities"]["win"] * 2.0

    # 2) Tiered exotic minimum anchor return gates ($12.50 Exacta / $10.00 Trifecta / $8.00 First 4)
    assert MIN_EXOTIC_RETURN == {"exacta": 12.50, "trifecta": 10.00, "first4": 8.00}

    # Construct market probs where Exacta return ~$11.00 (< $12.50 -> SKIP), Trifecta return ~$10.90 (>= $10.00 -> ACTIVE),
    # and First 4 return ~$9.00 (>= $8.00 -> ACTIVE, whereas old $12.50 flat floor would have skipped both!)
    mkt = {1: 0.42, 2: 0.25, 3: 0.14, 4: 0.09, 5: 0.04, 6: 0.03, 7: 0.02, 8: 0.01}

    ex = _build_single_exotic("exacta", "Exacta", [[1, 2], [1, 2, 3]], 4, [1, 2], 8, False, mkt)
    # anchor_prob = 0.42 * (0.25/0.58) = 0.1810 -> div ~4.36 -> return (4 combos, 250%) ~$10.91 < $12.50 -> SKIP
    assert ex["skipped"] is True and "$12.50 min" in ex["skip_reason"]

    tri = _build_single_exotic("trifecta", "Trifecta", [[1, 2], [1, 2, 3, 4], [1, 2, 3, 4, 5]], 18, [1, 2, 3], 8, False, mkt)
    # anchor_prob = 0.1810 * (0.14/0.33) = 0.0768 -> div ~10.29 -> return (18 combos) ~$5.72 < $10.00 -> SKIP
    assert tri["skipped"] is True and "$10.00 min" in tri["skip_reason"]

    # Slightly less top-heavy field: Trifecta ~$11.00 (ACTIVE >= $10.00) and First 4 ~$9.50 (ACTIVE >= $8.00)
    mkt_mid = {1: 0.34, 2: 0.22, 3: 0.14, 4: 0.10, 5: 0.07, 6: 0.05, 7: 0.04, 8: 0.04}
    tri_ok = _build_single_exotic("trifecta", "Trifecta", [[1, 2], [1, 2, 3, 4], [1, 2, 3, 4, 5]], 18, [1, 2, 3], 8, False, mkt_mid)
    assert tri_ok["skipped"] is False and 10.00 <= tri_ok["est_anchor_return"] < 14.00

    f4_mkt = {1: 0.38, 2: 0.24, 3: 0.16, 4: 0.10, 5: 0.04, 6: 0.03, 7: 0.03, 8: 0.02}
    f4_ok = _build_single_exotic("first4", "First 4", [[1, 2], [1, 2, 3, 4], [1, 2, 3, 4], [1, 2, 3, 4, 5]], 24, [1, 2, 3, 4], 8, False, f4_mkt)
    # anchor_prob = 0.38*(0.24/0.62)*(0.16/0.38)*(0.10/0.22) = 0.02815 -> div ~28.06 -> return (24 combos) ~$11.69 >= $8.00 -> ACTIVE
    assert f4_ok["skipped"] is False and f4_ok["est_anchor_return"] >= 8.00

    f4_low_mkt = {1: 0.45, 2: 0.28, 3: 0.15, 4: 0.08, 5: 0.01, 6: 0.01, 7: 0.01, 8: 0.01}
    f4_skip = _build_single_exotic("first4", "First 4", [[1, 2], [1, 2, 3, 4], [1, 2, 3, 4], [1, 2, 3, 4, 5]], 24, [1, 2, 3, 4], 8, False, f4_low_mkt)
    assert f4_skip["skipped"] is True and "$8.00 min" in f4_skip["skip_reason"]
