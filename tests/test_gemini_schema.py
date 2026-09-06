from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.genai._transformers import t_schema

from clipradar.ai.gemini import (
    GEMINI_MODELS,
    ClipEvaluation,
    FramingEvaluation,
    GeminiClient,
    GeminiError,
)
from clipradar.models import ClipCandidate


def test_clip_evaluation_schema_is_accepted_by_google_sdk():
    schema = t_schema(None, ClipEvaluation)

    assert schema is not None
    assert schema.properties is not None
    assert schema.properties["end_offset_seconds"].minimum == 0
    assert "tags" in schema.properties
    assert "reframe_mode" in schema.properties
    assert "focus_x" in schema.properties
    assert "gameplay_present" in schema.properties
    assert "hud_width" in schema.properties


def test_framing_schema_is_accepted_by_google_sdk():
    schema = t_schema(None, FramingEvaluation)

    assert schema is not None
    assert schema.properties is not None
    assert "facecam_present" in schema.properties
    assert "gameplay_present" in schema.properties
    assert "hud_present" in schema.properties
    assert schema.properties["focus_x"].minimum == 0
    assert schema.properties["focus_x"].maximum == 1000


def test_gemini_prompt_requests_editable_metadata_tags_and_scene_focus():
    candidate = ClipCandidate(None, 1, 10, 45, 88, {"transcript_excerpt": "That was impossible!"})
    prompt = GeminiClient._prompt(candidate, 20, 60, "Detailed", "English", "Gaming")
    assert "500-1200 characters" in prompt
    assert "6-15 specific search tags" in prompt
    assert "random foreign-language text" in prompt
    assert "important gameplay area" in prompt
    assert "Write metadata in English" in prompt
    assert "explicitly classified this source video as Gaming" in prompt


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


def _valid_framing_payload(mode="focus"):
    return {
        "reason": "The relevant reaction is left of the old center crop.",
        "reframe_mode": mode,
        "focus_x": 270,
        "focus_y": 440,
        "facecam_present": mode == "gaming_split",
        "facecam_x": 25 if mode == "gaming_split" else 0,
        "facecam_y": 40 if mode == "gaming_split" else 0,
        "facecam_width": 180 if mode == "gaming_split" else 0,
        "facecam_height": 240 if mode == "gaming_split" else 0,
        "gameplay_present": mode == "gaming_split",
        "gameplay_x": 0,
        "gameplay_y": 0,
        "gameplay_width": 1000 if mode == "gaming_split" else 0,
        "gameplay_height": 1000 if mode == "gaming_split" else 0,
        "hud_present": mode == "gaming_split",
        "hud_x": 760 if mode == "gaming_split" else 0,
        "hud_y": 80 if mode == "gaming_split" else 0,
        "hud_width": 200 if mode == "gaming_split" else 0,
        "hud_height": 240 if mode == "gaming_split" else 0,
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


def _analyze_framing(monkeypatch, tmp_path, model, responses, requested_mode="focus"):
    from google import genai

    fake = _FakeClient(responses)
    monkeypatch.setattr(genai, "Client", lambda **_kwargs: fake)
    original = tmp_path / "original.mp4"
    current = tmp_path / "current.mp4"
    original.write_bytes(b"original-unit-test-video")
    current.write_bytes(b"current-unit-test-video")
    candidate = ClipCandidate(
        None,
        1,
        10,
        40,
        82,
        {},
        reframe_mode="center",
        focus_x=0.5,
        focus_y=0.5,
    )
    result = GeminiClient().analyze_framing(
        api_key="unit-test-key",
        model=model,
        original_preview_path=original,
        current_preview_path=current,
        requested_mode=requested_mode,
        candidate=candidate,
        temperature=0.2,
    )
    return result, fake


@pytest.mark.parametrize("_label,model", GEMINI_MODELS)
def test_every_selectable_model_uses_the_same_validated_output_path(monkeypatch, tmp_path, _label, model):
    result, fake = _analyze(monkeypatch, tmp_path, model, [_response(parsed=_valid_payload())])
    assert result.score == 91
    assert result.request_count == 1
    assert fake.models.calls[0]["model"] == model
    assert fake.models.calls[0]["config"].max_output_tokens == 8192


@pytest.mark.parametrize("_label,model", GEMINI_MODELS)
def test_every_selectable_model_uses_framing_comparison_path(monkeypatch, tmp_path, _label, model):
    result, fake = _analyze_framing(
        monkeypatch,
        tmp_path,
        model,
        [_response(parsed=_valid_framing_payload())],
    )
    assert result.reframe_mode == "focus"
    assert result.focus_x == pytest.approx(0.27)
    assert fake.models.calls[0]["model"] == model
    assert fake.models.calls[0]["config"].max_output_tokens == 2048
    assert len(fake.models.calls[0]["contents"]) == 3


def test_framing_prompt_reconsiders_important_subject_and_compares_both_renders():
    candidate = ClipCandidate(None, 1, 10, 40, 82, {}, reframe_mode="center")

    prompt = GeminiClient._framing_prompt(candidate, "focus")

    assert "ORIGINAL SOURCE SEGMENT" in prompt
    assert "CURRENT\nVERTICAL RENDER" in prompt
    assert "independently reconsider" in prompt
    assert "geometric center" in prompt


def test_explicit_important_subject_mode_cannot_be_changed_by_model(monkeypatch, tmp_path):
    result, _fake = _analyze_framing(
        monkeypatch,
        tmp_path,
        "gemini-3.5-flash-lite",
        [_response(parsed=_valid_framing_payload("gaming_split"))],
        requested_mode="focus",
    )

    assert result.reframe_mode == "focus"
    assert result.facecam_x is None


def test_facecam_gameplay_requires_a_real_facecam_box(monkeypatch, tmp_path):
    payload = _valid_framing_payload("gaming_split")
    payload.update(facecam_present=False, facecam_width=0, facecam_height=0)

    with pytest.raises(GeminiError, match="could not locate a valid facecam"):
        _analyze_framing(
            monkeypatch,
            tmp_path,
            "gemini-3.5-flash-lite",
            [_response(parsed=payload)],
            requested_mode="gaming_split",
        )


def test_framing_result_preserves_gameplay_and_hud_regions(monkeypatch, tmp_path):
    result, _fake = _analyze_framing(
        monkeypatch,
        tmp_path,
        "gemini-3.5-flash-lite",
        [_response(parsed=_valid_framing_payload("gaming_split"))],
        requested_mode="gaming_split",
    )

    assert result.gameplay_width == pytest.approx(1.0)
    assert result.hud_x == pytest.approx(0.76)
    assert result.hud_width == pytest.approx(0.20)


def test_incomplete_framing_response_retries_with_compact_larger_budget(monkeypatch, tmp_path):
    result, fake = _analyze_framing(
        monkeypatch,
        tmp_path,
        "gemini-3.8-flash",
        [
            _response(text='{"reason":"unfinished', finish_reason="MAX_TOKENS", thoughts=120),
            _response(parsed=_valid_framing_payload(), input_tokens=110, output_tokens=55),
        ],
    )

    assert result.request_count == 2
    assert [call["config"].max_output_tokens for call in fake.models.calls] == [2048, 4096]
    assert fake.models.calls[1]["config"].temperature == 0


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
