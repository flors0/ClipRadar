from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from clipradar.models import ClipCandidate


GEMINI_MODELS = [
    ("Gemini 3.8 Flash · highest quality", "gemini-3.8-flash"),
    ("Gemini 3.7 Flash · quality", "gemini-3.7-flash"),
    ("Gemini 3.5 Flash · balanced", "gemini-3.5-flash"),
    ("Gemini 3.5 Flash-Lite · lowest cost", "gemini-3.5-flash-lite"),
    ("Gemini 3.1 Flash-Lite · efficient", "gemini-3.1-flash-lite"),
    ("Gemini 2.5 Flash · legacy stable", "gemini-2.5-flash"),
]


class GeminiError(RuntimeError):
    pass


class ClipEvaluation(BaseModel):
    score: int = Field(ge=0, le=100)
    reason: str = Field(min_length=3, max_length=280)
    start_offset_seconds: float = Field(ge=0)
    end_offset_seconds: float = Field(gt=0)
    works_without_context: bool
    hook: str = Field(default="", max_length=140)


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    score: int
    reason: str
    refined_start: float
    refined_end: float
    input_tokens: int
    output_tokens: int
    estimated_cost_eur: float


class GeminiClient:
    def test_connection(self, api_key: str, model: str) -> str:
        if not api_key.strip():
            raise GeminiError("Enter a Gemini API key first.")
        try:
            from google import genai

            with genai.Client(api_key=api_key.strip()) as client:
                resolved = client.models.get(model=model)
            name = str(getattr(resolved, "display_name", None) or getattr(resolved, "name", model))
            return f"Connected · {name}"
        except Exception as exc:
            raise GeminiError(self._safe_message(exc, api_key)) from exc

    def analyze_candidate(
        self,
        *,
        api_key: str,
        model: str,
        preview_path: str | Path,
        candidate: ClipCandidate,
        source_duration: float,
        minimum_duration: float,
        maximum_duration: float,
        temperature: float,
    ) -> EvaluationResult:
        preview_path = Path(preview_path)
        if not api_key.strip():
            raise GeminiError("No Gemini API key is configured. Open Settings → AI.")
        data = preview_path.read_bytes()
        if len(data) > 19 * 1024 * 1024:
            raise GeminiError("Candidate preview exceeds the safe inline upload limit.")
        prompt = self._prompt(candidate, minimum_duration, maximum_duration)
        try:
            from google import genai
            from google.genai import types

            part = types.Part.from_bytes(data=data, mime_type="video/mp4")
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ClipEvaluation,
                temperature=max(0.0, min(1.0, temperature)),
                max_output_tokens=500,
            )
            with genai.Client(api_key=api_key.strip()) as client:
                response = client.models.generate_content(model=model, contents=[prompt, part], config=config)
            evaluation = response.parsed
            if not isinstance(evaluation, ClipEvaluation):
                evaluation = ClipEvaluation.model_validate(json.loads(response.text))
            usage = getattr(response, "usage_metadata", None)
            input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
            output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        except Exception as exc:
            raise GeminiError(self._safe_message(exc, api_key)) from exc

        candidate_duration = candidate.end_seconds - candidate.start_seconds
        start_offset = min(candidate_duration, max(0.0, evaluation.start_offset_seconds))
        end_offset = min(candidate_duration, max(start_offset + 1.0, evaluation.end_offset_seconds))
        refined_start = candidate.start_seconds + start_offset
        refined_end = min(source_duration, candidate.start_seconds + end_offset)
        if refined_end - refined_start < minimum_duration:
            midpoint = (refined_start + refined_end) / 2
            refined_start = max(0, midpoint - minimum_duration / 2)
            refined_end = min(source_duration, refined_start + minimum_duration)
            refined_start = max(0, refined_end - minimum_duration)
        if refined_end - refined_start > maximum_duration:
            refined_end = refined_start + maximum_duration
        reason = evaluation.reason.strip()
        if evaluation.hook.strip():
            reason = f"{reason} · Hook: {evaluation.hook.strip()}"
        return EvaluationResult(
            score=evaluation.score,
            reason=reason[:420],
            refined_start=round(refined_start, 3),
            refined_end=round(refined_end, 3),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_eur=estimate_cost_eur(model, input_tokens, output_tokens),
        )

    @staticmethod
    def _prompt(candidate: ClipCandidate, minimum_duration: float, maximum_duration: float) -> str:
        excerpt = str(candidate.signals.get("transcript_excerpt", ""))
        return f"""You are selecting one excellent short-form social video moment.
Watch the entire attached candidate. Score it for humor, surprise, emotion, a strong reaction,
a clear payoff, understandable context, and suitability for YouTube Shorts/TikTok.
Quality matters more than quantity. A mediocre but usable moment should score below 60.

Return offsets relative to the beginning of this attached candidate, not source-video timestamps.
Choose natural sentence/reaction boundaries. Keep the final duration between {minimum_duration:.0f}
and {maximum_duration:.0f} seconds whenever possible, but never cut off the payoff.

Local transcript (may contain errors): {excerpt or '[not available]'}
Local signal score: {candidate.local_score:.1f}/100
Candidate duration: {candidate.end_seconds - candidate.start_seconds:.2f} seconds.
"""

    @staticmethod
    def _safe_message(exc: Exception, api_key: str) -> str:
        message = str(exc).replace(api_key, "[redacted]") if api_key else str(exc)
        lowered = message.lower()
        if "api key" in lowered and any(token in lowered for token in ("invalid", "not valid", "permission")):
            return "Invalid API key or the selected model is not available to this key."
        if "429" in message or "quota" in lowered or "resource_exhausted" in lowered:
            return "Gemini quota or rate limit reached. No further candidate was sent."
        if "timed out" in lowered or "connect" in lowered:
            return "Connection to Gemini failed. Check the network and try again."
        return f"Gemini analysis failed: {message[:350]}"


def estimate_cost_eur(model: str, input_tokens: int, output_tokens: int) -> float:
    """Conservative client-side estimate. Actual billing remains provider authoritative."""
    lowered = model.lower()
    if "lite" in lowered:
        input_usd, output_usd = 0.30, 1.50
    elif "3.8" in lowered or "3.7" in lowered:
        input_usd, output_usd = 0.75, 3.75
    elif "pro" in lowered:
        input_usd, output_usd = 2.50, 15.00
    else:
        input_usd, output_usd = 0.60, 3.50
    usd = input_tokens / 1_000_000 * input_usd + output_tokens / 1_000_000 * output_usd
    return round(usd * 0.95, 6)

