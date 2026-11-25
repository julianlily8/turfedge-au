from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from pipeline.src.engine.scorer_23pt import NEUTRAL_SCORE, NO_DATA_LABEL, STRICT_TEXT_FACTORS, clamp, load_factor_weights

logger = logging.getLogger(__name__)

PRIMARY_MODEL = "gemini-3.1-flash-lite"
FALLBACK_MODEL = "gemini-3.1-flash-lite-preview"
MIN_GEMINI_SPACING_SECONDS = 10.0


class GeminiBatchAnalyzer:
    """
    Meeting-level batched Gemini API analyzer using gemini-3.1-flash-lite.
    - Enforces >=10-second spacing between requests to stay strictly within free-tier RPM/RPD/TPM limits.
    - Enforces strict anti-hallucination validation: factors without source text stay locked at 5.0 / 'No data available'.
    """

    def __init__(self, api_key: Optional[str] = None, min_spacing_seconds: float = MIN_GEMINI_SPACING_SECONDS) -> None:
        self.api_key = (api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
        self.min_spacing_seconds = max(10.0, float(min_spacing_seconds))
        self._last_call_ts: Optional[float] = None
        self.weights_cfg = load_factor_weights()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _wait_for_spacing(self) -> None:
        if self._last_call_ts is not None:
            elapsed = time.monotonic() - self._last_call_ts
            remaining = self.min_spacing_seconds - elapsed
            if remaining > 0:
                logger.info("Respecting Gemini free-tier spacing: sleeping %.1fs...", remaining)
                time.sleep(remaining)
        self._last_call_ts = time.monotonic()

    def _build_meeting_prompt(
        self,
        meeting_name: str,
        weather: Dict[str, Any],
        meeting_bias: Dict[str, Any],
        races: List[Dict[str, Any]],
        youtube_transcript: str = "",
    ) -> str:
        compact_races = []
        for race in races:
            runners_payload = []
            for r in race.get("scored_runners", []):
                audit_map = {a["key"]: a for a in r.get("audit_23pt", [])}
                runners_payload.append(
                    {
                        "runner_number": r["runner_number"],
                        "name": r["name"],
                        "barrier": r["barrier"],
                        "jockey": r["jockey"],
                        "trainer": r["trainer"],
                        "weight_carried": r["weight_carried"],
                        "gear": r.get("gear") or "",
                        "sire": r.get("sire") or "",
                        "dam": r.get("dam") or "",
                        "speedmap": r.get("speedmap_label") or "",
                        "last_twenty": r.get("last_twenty_starts") or "",
                        "fluc": r.get("market_fluc"),
                        "form_comment": r.get("form_comment") or None,
                        "stewards_notes": r.get("stewards_notes") or [],
                        "vet_notes": r.get("vet_notes") or [],
                        "has_trials": audit_map.get("recent_trials", {}).get("has_data", False),
                        "baseline_score": r.get("composite_score"),
                    }
                )
            compact_races.append(
                {
                    "race_id": race["race_id"],
                    "race_number": race["race_number"],
                    "name": race["name"],
                    "distance": race["distance"],
                    "track_condition": race["track_condition"],
                    "rail_position": race["rail_position"],
                    "race_comment": (race.get("race_comment") or "")[:900],
                    "runners": runners_payload,
                }
            )

        transcript_excerpt = youtube_transcript[:5000] if youtube_transcript else "NONE PROVIDED"

        return (
            "You are an Australian thoroughbred racing quantitative analyst.\n"
            "STRICT RULES (MANDATORY):\n"
            "1. NEVER fabricate stewards reports, jumpout/trial results, vet/injury history, or trainer/jockey quotes.\n"
            "2. If a runner has NO stewards_notes, NO vet_notes, NO form_comment/race_comment mention, NO trials, or NO YouTube mention, "
            "you MUST leave those factors at score 5.0 with reason 'No data available'.\n"
            "3. Only adjust qualitative/contextual factors grounded strictly in the provided JSON and transcript below.\n"
            "4. Return valid JSON ONLY matching this exact schema:\n"
            '{"races": [{"race_id": "string", "race_summary": "1-2 sentence synthesis", "runners": [{'
            '"runner_number": 1, "summary_note": "1 concise sentence", '
            '"factor_overrides": {"pedigree_suitability": {"score": 6.5, "reason": "..."}, '
            '"stewards_comments": {"score": 5.0, "reason": "No data available"}, '
            '"trainer_jockey_commentary": {"score": 5.0, "reason": "No data available"}, '
            '"injury_history": {"score": 5.0, "reason": "No data available"}, '
            '"recent_trials": {"score": 5.0, "reason": "No data available"}, '
            '"gear_changes": {"score": 5.5, "reason": "..."}, '
            '"target_race_prep": {"score": 6.0, "reason": "..."}, '
            '"youtube_transcripts": {"score": 5.0, "reason": "No data available"}}}]}]}\n\n'
            f"MEETING: {meeting_name}\n"
            f"WEATHER: {json.dumps(weather)}\n"
            f"MEETING_BIAS: {json.dumps(meeting_bias)}\n"
            f"YOUTUBE_TRANSCRIPT: {transcript_excerpt}\n"
            f"RACES_DATA: {json.dumps(compact_races)}"
        )

    def analyze_meeting_batch(
        self,
        meeting_name: str,
        weather: Dict[str, Any],
        meeting_bias: Dict[str, Any],
        races: List[Dict[str, Any]],
        youtube_transcript: str = "",
    ) -> Dict[str, Any]:
        """Batch-analyze all provided races in a meeting via Gemini 3.1 Flash Lite (chunks of <=5 races to stay well under TPM)."""
        if not self.is_configured or not races:
            logger.info("Gemini API key not configured or no races to analyze; keeping deterministic 23-point scores.")
            return {"used_gemini": False, "model": None, "races_updated": 0}

        try:
            from google import genai
            from google.genai import types
        except ImportError:
            logger.warning("google-genai package not installed; falling back to deterministic scores.")
            return {"used_gemini": False, "model": None, "races_updated": 0}

        client = genai.Client(api_key=self.api_key)
        chunk_size = 5
        updated_count = 0
        used_model = PRIMARY_MODEL

        for i in range(0, len(races), chunk_size):
            chunk = races[i : i + chunk_size]
            prompt = self._build_meeting_prompt(meeting_name, weather, meeting_bias, chunk, youtube_transcript)
            parsed = self._call_gemini_with_retry(client, types, prompt)
            if not parsed:
                continue
            used_model = parsed.get("_model", PRIMARY_MODEL)
            ai_races = {str(r.get("race_id")): r for r in (parsed.get("races") or []) if isinstance(r, dict)}
            for race in chunk:
                ai_r = ai_races.get(str(race["race_id"]))
                if not ai_r:
                    continue
                race["ai_summary"] = (ai_r.get("race_summary") or "").strip()
                race["ai_model"] = used_model
                self._apply_validated_overrides(race, ai_r, youtube_transcript)
                updated_count += 1

        return {"used_gemini": updated_count > 0, "model": used_model if updated_count else None, "races_updated": updated_count}

    def _call_gemini_with_retry(self, client: Any, types: Any, prompt: str) -> Optional[Dict[str, Any]]:
        models_to_try = [PRIMARY_MODEL, FALLBACK_MODEL]
        for model_id in models_to_try:
            for attempt in range(3):
                self._wait_for_spacing()
                try:
                    resp = client.models.generate_content(
                        model=model_id,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.2,
                        ),
                    )
                    text = (getattr(resp, "text", None) or "").strip()
                    if text:
                        data = json.loads(text)
                        if isinstance(data, dict):
                            data["_model"] = model_id
                            return data
                except Exception as exc:
                    err_str = str(exc)
                    if "404" in err_str or "NOT_FOUND" in err_str:
                        logger.warning("Model %s not found; trying fallback model...", model_id)
                        break
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                        wait_s = 15.0 * (attempt + 1)
                        logger.warning("Gemini 429 rate limit on %s; sleeping %.0fs...", model_id, wait_s)
                        time.sleep(wait_s)
                        continue
                    logger.warning("Gemini call error on %s (attempt %d): %s", model_id, attempt + 1, exc)
                    time.sleep(10.0)
        return None

    def _apply_validated_overrides(self, race: Dict[str, Any], ai_race: Dict[str, Any], youtube_transcript: str) -> None:
        """Merge Gemini qualitative adjustments while strictly blocking fabricated data on missing text fields."""
        ai_runners = {int(r.get("runner_number") or 0): r for r in (ai_race.get("runners") or []) if isinstance(r, dict)}
        total_weight = sum(float(f["weight"]) for f in self.weights_cfg["factors"]) or 100.0
        allowed_override_keys = {
            "pedigree_suitability",
            "stewards_comments",
            "trainer_jockey_commentary",
            "injury_history",
            "recent_trials",
            "gear_changes",
            "target_race_prep",
            "youtube_transcripts",
        }

        for runner in race.get("scored_runners", []):
            ai_r = ai_runners.get(int(runner["runner_number"]))
            if not ai_r:
                continue
            runner["ai_note"] = (ai_r.get("summary_note") or "").strip()
            overrides = ai_r.get("factor_overrides") or {}
            if not isinstance(overrides, dict):
                continue

            composite = 0.0
            for item in runner.get("audit_23pt", []):
                key = item["key"]
                # Anti-hallucination hard guard: never let Gemini override a missing text field!
                if key in STRICT_TEXT_FACTORS and not item.get("has_data"):
                    item["score"] = NEUTRAL_SCORE
                    item["reason"] = NO_DATA_LABEL
                elif key in allowed_override_keys and key in overrides and isinstance(overrides[key], dict):
                    ov = overrides[key]
                    if key == "youtube_transcripts" and not youtube_transcript:
                        item["score"] = NEUTRAL_SCORE
                        item["reason"] = NO_DATA_LABEL
                    else:
                        try:
                            new_sc = clamp(float(ov.get("score", item["score"])))
                            # Blend 60% Gemini / 40% deterministic for stability
                            item["score"] = clamp(0.6 * new_sc + 0.4 * float(item["score"]))
                        except (TypeError, ValueError):
                            pass
                        ov_reason = str(ov.get("reason") or "").strip()
                        if ov_reason:
                            item["reason"] = ov_reason[:160]

                item["weighted_points"] = round((float(item["score"]) / 10.0) * float(item["weight"]), 3)
                composite += item["weighted_points"]

            runner["composite_score"] = round((composite / total_weight) * 100.0, 2)

        race["scored_runners"].sort(key=lambda x: x["composite_score"], reverse=True)
