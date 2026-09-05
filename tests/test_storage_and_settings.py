from __future__ import annotations

import json
import logging
import sqlite3

from clipradar.app.logging_setup import RedactingFilter
from clipradar.models import Channel
from clipradar.settings.models import AISettings, BudgetSettings, ClipSettings
from clipradar.storage.database import Database


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


def test_version_one_database_migrates_metadata_publishing_and_disables_captions(tmp_path):
    path = tmp_path / "version-one.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_info (version INTEGER NOT NULL);
            INSERT INTO schema_info(version) VALUES (1);
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE clip_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_video_id INTEGER NOT NULL,
                start_seconds REAL NOT NULL,
                end_seconds REAL NOT NULL,
                local_score REAL NOT NULL,
                signals_json TEXT NOT NULL DEFAULT '{}',
                ai_score REAL,
                ai_reason TEXT NOT NULL DEFAULT '',
                refined_start_seconds REAL,
                refined_end_seconds REAL,
                status TEXT NOT NULL DEFAULT 'Detected'
            );
            """
        )
        connection.execute(
            "INSERT INTO settings(key, value_json, updated_at) VALUES ('clips', ?, '2026-01-01')",
            (json.dumps({"captions_enabled": True, "word_highlighting": True}),),
        )

    database = Database(path)
    database.initialize()
    with database.connection() as connection:
        version = connection.execute("SELECT version FROM schema_info").fetchone()["version"]
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(clip_candidates)")}
        settings = json.loads(
            connection.execute("SELECT value_json FROM settings WHERE key = 'clips'").fetchone()["value_json"]
        )
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert version == 2
    assert {"ai_title", "ai_tags_json", "reframe_mode", "focus_x", "facecam_x"} <= columns
    assert {"youtube_accounts", "publish_jobs"} <= tables
    assert settings["captions_enabled"] is False
    assert settings["word_highlighting"] is False


def test_logging_filter_redacts_google_tokens():
    record = logging.LogRecord(
        "clipradar.test",
        logging.ERROR,
        __file__,
        1,
        'refresh_token="1//abcdefghijklmnopqrstuvwxyz" access_token=ya29.abcdefghijklmnopqrstuvwxyz',
        (),
        None,
    )
    assert RedactingFilter().filter(record)
    message = record.getMessage()
    assert "abcdefghijklmnopqrstuvwxyz" not in message
    assert "[redacted]" in message


def test_budget_failure_explains_used_requested_and_configured_minutes(services):
    services.repositories.usage.add(source_minutes=240)
    decision = services.pipeline.budget.can_start_video(
        120 * 60,
        BudgetSettings(max_source_minutes_per_day=300),
    )
    assert not decision.allowed
    assert "240.0 used + 120.0" in decision.reason
    assert "> 300 minutes" in decision.reason
