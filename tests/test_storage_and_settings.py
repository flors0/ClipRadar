from __future__ import annotations

import json
import logging
import sqlite3

from clipradar.app.logging_setup import RedactingFilter
from clipradar.models import Channel, FramingProfile, JobStatus
from clipradar.settings.models import AISettings, BudgetSettings, ClipSettings, UIStateSettings
from clipradar.storage.database import Database


def test_database_and_settings_survive_restart(services):
    services.settings.save_ai(AISettings(model="gemini-3.8-flash", minimum_ai_score=70))
    services.settings.save_clips(ClipSettings(minimum_duration=10, target_duration=20, maximum_duration=30))
    services.settings.save_ui_state(UIStateSettings(review_channel_id=7, dashboard_channel_id=9))
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
    assert restarted.settings.ui_state().review_channel_id == 7
    assert restarted.settings.ui_state().dashboard_channel_id == 9
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
    assert version == 7
    assert {"ai_title", "ai_tags_json", "reframe_mode", "focus_x", "facecam_x"} <= columns
    assert "output_layout_json" in columns
    assert {"framing_profiles", "youtube_accounts", "publish_jobs"} <= tables
    assert settings["captions_enabled"] is False
    assert settings["word_highlighting"] is False


def test_version_two_database_adds_source_video_category(tmp_path):
    path = tmp_path / "version-two.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_info (version INTEGER NOT NULL);
            INSERT INTO schema_info(version) VALUES (2);
            CREATE TABLE source_videos (id INTEGER PRIMARY KEY AUTOINCREMENT);
            """
        )

    database = Database(path)
    database.initialize()
    with database.connection() as connection:
        version = connection.execute("SELECT version FROM schema_info").fetchone()["version"]
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(source_videos)")}

    assert version == 7
    assert "category_id" in columns


def test_version_three_database_adds_review_buffers_and_semantic_regions(tmp_path):
    path = tmp_path / "version-three.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_info (version INTEGER NOT NULL);
            INSERT INTO schema_info(version) VALUES (3);
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
            CREATE TABLE rendered_clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL,
                source_video_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                format TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Ready',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO clip_candidates
                (source_video_id, start_seconds, end_seconds, local_score,
                 refined_start_seconds, refined_end_seconds)
                VALUES (1, 10, 40, 80, 12, 38);
            INSERT INTO rendered_clips
                (candidate_id, source_video_id, file_path, duration_seconds, format,
                 status, created_at, updated_at)
                VALUES (1, 1, 'old.mp4', 26, 'Vertical 9:16', 'Ready', '2026-01-01', '2026-01-01');
            """
        )

    database = Database(path)
    database.initialize()
    with database.connection() as connection:
        version = connection.execute("SELECT version FROM schema_info").fetchone()["version"]
        candidate_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(clip_candidates)")
        }
        clip_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(rendered_clips)")
        }
        migrated = connection.execute(
            "SELECT buffer_start_seconds, buffer_end_seconds FROM rendered_clips WHERE id = 1"
        ).fetchone()

    assert version == 7
    assert {"gameplay_x", "gameplay_height", "hud_x", "hud_height"} <= candidate_columns
    assert {"buffer_start_seconds", "buffer_end_seconds"} <= clip_columns
    assert migrated["buffer_start_seconds"] == 12
    assert migrated["buffer_end_seconds"] == 38


def test_version_four_database_requires_approval_for_legacy_automatic_jobs(tmp_path):
    path = tmp_path / "version-four.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_info (version INTEGER NOT NULL);
            INSERT INTO schema_info(version) VALUES (4);
            CREATE TABLE analysis_jobs (
                id TEXT PRIMARY KEY,
                source_video_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                manual INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                progress REAL NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE clip_candidates (
                id INTEGER PRIMARY KEY,
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
            CREATE TABLE rendered_clips (
                id INTEGER PRIMARY KEY,
                candidate_id INTEGER NOT NULL,
                source_video_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                format TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Ready',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                buffer_start_seconds REAL,
                buffer_end_seconds REAL
            );
            INSERT INTO analysis_jobs VALUES
                ('automatic', 1, 'Scheduled', 'Queued', '2026-01-01', 0, 0, 0, NULL, '2026-01-01', '2026-01-01'),
                ('manual', 1, 'Waiting', 'Queued', '2026-01-01', 1, 0, 0, NULL, '2026-01-01', '2026-01-01');
            INSERT INTO clip_candidates
                (id, source_video_id, start_seconds, end_seconds, local_score,
                 refined_start_seconds, refined_end_seconds)
                VALUES (1, 1, 10, 40, 80, 12, 38);
            INSERT INTO rendered_clips VALUES
                (1, 1, 1, 'review.mp4', 86, 'Vertical 9:16', 'Ready',
                 '2026-01-01', '2026-01-01', 0, 86);
            """
        )

    database = Database(path)
    database.initialize()
    with database.connection() as connection:
        jobs = {
            row["id"]: row
            for row in connection.execute(
                "SELECT id, approved, cancel_requested FROM analysis_jobs"
            )
        }
        clip = connection.execute(
            "SELECT trim_origin_seconds FROM rendered_clips WHERE id = 1"
        ).fetchone()
        version = connection.execute("SELECT version FROM schema_info").fetchone()["version"]

    assert version == 7
    assert jobs["automatic"]["approved"] == 0
    assert jobs["manual"]["approved"] == 1
    assert jobs["automatic"]["cancel_requested"] == 0
    assert clip["trim_origin_seconds"] == 12


def test_analysis_job_can_be_started_and_cancelled_before_work(services):
    channel = services.repositories.channels.add(Channel(
        None, "UC_TASK", "Task Channel", "", "https://youtube.test/task"
    ))
    from clipradar.models import SourceVideo, utc_now

    source, _ = services.repositories.videos.upsert(SourceVideo(
        None, int(channel.id), "task-video", "Task video", "https://youtube.test/task-video"
    ))
    job = services.repositories.jobs.create(int(source.id), utc_now(), manual=False)
    assert not job.approved
    assert services.repositories.jobs.next_due() is None
    assert services.repositories.jobs.approve(job.id)
    assert services.repositories.jobs.next_due().id == job.id
    assert services.repositories.jobs.request_cancel(job.id)
    cancelled = services.repositories.jobs.get(job.id)
    assert cancelled.status == JobStatus.CANCELLED
    assert cancelled.cancel_requested
    assert cancelled.stage == "Stopped"


def test_channel_framing_profile_survives_restart(services):
    channel = services.repositories.channels.add(Channel(
        None,
        "UC_PROFILE",
        "Profile Channel",
        "",
        "https://youtube.test/profile",
        clip_selection_instructions="Prefer decisive outplays with a payoff.",
    ))
    saved = services.repositories.framing_profiles.save(FramingProfile(
        None,
        int(channel.id),
        "gaming_split",
        instructions="Keep the scoreboard next to the facecam.",
        facecam_x=0.03,
        facecam_y=0.08,
        facecam_width=0.18,
        facecam_height=0.23,
        gameplay_x=0.0,
        gameplay_y=0.0,
        gameplay_width=1.0,
        gameplay_height=1.0,
        hud_x=0.78,
        hud_y=0.08,
        hud_width=0.18,
        hud_height=0.22,
        output_regions={
            "facecam": (0.0, 0.0, 0.45, 0.28),
            "hud": (0.45, 0.0, 0.55, 0.28),
            "gameplay": (0.0, 0.28, 1.0, 0.72),
        },
    ))

    restarted = type(services).create(services.paths, services.settings.secrets)
    loaded = restarted.repositories.framing_profiles.get(int(channel.id), "gaming_split")

    assert saved.id is not None
    assert loaded is not None
    assert loaded.instructions == "Keep the scoreboard next to the facecam."
    assert loaded.facecam_x == 0.03
    assert loaded.hud_width == 0.18
    assert loaded.output_regions["hud"] == (0.45, 0.0, 0.55, 0.28)
    assert restarted.repositories.channels.get(int(channel.id)).clip_selection_instructions.startswith(
        "Prefer decisive"
    )


def test_version_six_database_adds_selection_and_output_layout_storage(tmp_path):
    path = tmp_path / "version-six.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_info (version INTEGER NOT NULL);
            INSERT INTO schema_info(version) VALUES (6);
            CREATE TABLE channels (id INTEGER PRIMARY KEY);
            CREATE TABLE framing_profiles (id INTEGER PRIMARY KEY);
            CREATE TABLE clip_candidates (id INTEGER PRIMARY KEY);
            """
        )

    database = Database(path)
    database.initialize()
    with database.connection() as connection:
        version = connection.execute("SELECT version FROM schema_info").fetchone()["version"]
        channel_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(channels)")
        }
        profile_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(framing_profiles)")
        }
        candidate_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(clip_candidates)")
        }

    assert version == 7
    assert "clip_selection_instructions" in channel_columns
    assert "output_layout_json" in profile_columns
    assert "output_layout_json" in candidate_columns


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
