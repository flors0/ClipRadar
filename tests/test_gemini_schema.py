from __future__ import annotations

from google.genai._transformers import t_schema

from clipradar.ai.gemini import ClipEvaluation, GeminiClient
from clipradar.models import ClipCandidate


def test_clip_evaluation_schema_is_accepted_by_google_sdk():
    schema = t_schema(None, ClipEvaluation)

    assert schema is not None
    assert schema.properties is not None
    assert schema.properties["end_offset_seconds"].minimum == 0
    assert "tags" in schema.properties
    assert "reframe_mode" in schema.properties
    assert "focus_x" in schema.properties


def test_gemini_prompt_requests_editable_metadata_tags_and_scene_focus():
    candidate = ClipCandidate(None, 1, 10, 45, 88, {"transcript_excerpt": "That was impossible!"})
    prompt = GeminiClient._prompt(candidate, 20, 60, "Detailed", "English")
    assert "500-1200 characters" in prompt
    assert "6-15 specific search tags" in prompt
    assert "random foreign-language text" in prompt
    assert "important gameplay area" in prompt
    assert "Write metadata in English" in prompt
