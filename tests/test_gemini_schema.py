from __future__ import annotations

from google.genai._transformers import t_schema

from clipradar.ai.gemini import ClipEvaluation


def test_clip_evaluation_schema_is_accepted_by_google_sdk():
    schema = t_schema(None, ClipEvaluation)

    assert schema is not None
    assert schema.properties is not None
    assert schema.properties["end_offset_seconds"].minimum == 0
