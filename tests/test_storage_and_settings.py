from __future__ import annotations

from clipradar.models import Channel
from clipradar.settings.models import AISettings, ClipSettings


def test_database_and_settings_survive_restart(services):
    services.settings.save_ai(AISettings(model="gemini-3.8-flash", minimum_ai_score=70))
    services.settings.save_clips(ClipSettings(minimum_duration=10, target_duration=20, maximum_duration=30))
    saved = services.repositories.channels.add(Channel(
        id=None,
        channel_id="UC_TEST_CHANNEL_00000001",
        name="Test Channel",
        avatar_url="https://example.test/avatar.png",
        url="https://youtube.com/channel/UC_TEST_CHANNEL_00000001",
    ))
    restarted = type(services).create(services.paths, services.settings.secrets)
    assert restarted.settings.ai().model == "gemini-3.8-flash"
    assert restarted.settings.clips().target_duration == 20
    assert restarted.repositories.channels.get(int(saved.id)).name == "Test Channel"


def test_secret_is_not_written_to_sqlite(services):
    secret = "AQ.unit-test-secret-that-must-not-be-in-db"
    services.settings.secrets.set_gemini_key(secret)
    services.settings.save_ai(AISettings(model="gemini-3.5-flash-lite"))
    assert secret.encode() not in services.paths.database.read_bytes()


def test_invalid_clip_duration_is_rejected(services):
    try:
        services.settings.save_clips(ClipSettings(minimum_duration=60, target_duration=20, maximum_duration=30))
    except ValueError as exc:
        assert "minimum" in str(exc)
    else:
        raise AssertionError("invalid duration settings were accepted")

