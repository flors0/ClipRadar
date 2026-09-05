from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.genai._transformers import t_schema

from clipradar.ai.gemini import GEMINI_MODELS, ClipEvaluation, GeminiClient, GeminiError
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


class _FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.models = _FakeModels(responses)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _response(*, parsed=None, text="", finish_reason="STOP", input_tokens=100, output_tokens=40, thoughts=0):
    return SimpleNamespace(
        parsed=parsed,
        text=text,
        candidates=[SimpleNamespace(finish_reason=finish_reason)],
        usage_metadata=SimpleNamespace(
            prompt_token_count=input_tokens,
            candidates_token_count=output_tokens,
            thoughts_token_count=thoughts,
        ),
    )


def _valid_payload():
    return {
        "score": 91,
        "reason": "A complete reaction with a clear payoff.",
        "start_offset_seconds": 1,
        "end_offset_seconds": 26,
        "works_without_context": True,
        "title": "A Perfect Reaction",
        "description": "The moment lands without extra context.",
        "tags": ["reaction", "shorts"],
        "reframe_mode": "focus",
        "focus_x": 610,
        "focus_y": 430,
    }


def _analyze(monkeypatch, tmp_path, model, responses, events=None):
    from google import genai

    fake = _FakeClient(responses)
    monkeypatch.setattr(genai, "Client", lambda **_kwargs: fake)
    preview = tmp_path / "candidate.mp4"
    preview.write_bytes(b"unit-test-video")
    result = GeminiClient().analyze_candidate(
        api_key="unit-test-key",
        model=model,
        preview_path=preview,
        candidate=ClipCandidate(None, 1, 10, 40, 82, {}),
        source_duration=120,
        minimum_duration=20,
        maximum_duration=60,
        temperature=0.2,
        event_callback=(lambda message, level: events.append((message, level))) if events is not None else None,
    )
    return result, fake


@pytest.mark.parametrize("_label,model", GEMINI_MODELS)
def test_every_selectable_model_uses_the_same_validated_output_path(monkeypatch, tmp_path, _label, model):
    result, fake = _analyze(monkeypatch, tmp_path, model, [_response(parsed=_valid_payload())])
    assert result.score == 91
    assert result.request_count == 1
    assert fake.models.calls[0]["model"] == model
    assert fake.models.calls[0]["config"].max_output_tokens == 8192


def test_truncated_structured_output_retries_once_with_larger_limit(monkeypatch, tmp_path):
    events = []
    result, fake = _analyze(
        monkeypatch,
        tmp_path,
        "gemini-3.8-flash",
        [
            _response(text='{"score":91,"reason":"unfinished', finish_reason="MAX_TOKENS", thoughts=300),
            _response(parsed=_valid_payload(), input_tokens=110, output_tokens=55, thoughts=20),
        ],
        events,
    )
    assert result.request_count == 2
    assert result.input_tokens == 210
    assert result.output_tokens == 415
    assert [call["config"].max_output_tokens for call in fake.models.calls] == [8192, 16384]
    assert fake.models.calls[1]["config"].temperature == 0
    assert any("MAX_TOKENS" in message and level == "warning" for message, level in events)


def test_malformed_stop_response_is_retried_instead_of_leaking_json_error(monkeypatch, tmp_path):
    result, _fake = _analyze(
        monkeypatch,
        tmp_path,
        "gemini-3.8-flash",
        [
            _response(text='{"score":91,"reason":"unterminated', finish_reason="STOP"),
            _response(parsed=_valid_payload()),
        ],
    )
    assert result.score == 91
    assert result.request_count == 2


def test_two_incomplete_responses_raise_a_clear_model_independent_error(monkeypatch, tmp_path):
    with pytest.raises(GeminiError, match="incomplete structured response") as captured:
        _analyze(
            monkeypatch,
            tmp_path,
            "gemini-3.5-flash-lite",
            [
                _response(text='{"reason":"broken', finish_reason="STOP"),
                _response(text='{"reason":"still broken', finish_reason="MAX_TOKENS"),
            ],
        )
    assert "Unterminated string" not in str(captured.value)
    assert captured.value.request_count == 2
