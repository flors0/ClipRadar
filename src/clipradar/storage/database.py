from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 4


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    avatar_url TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    monitoring_enabled INTEGER NOT NULL DEFAULT 1,
    last_checked_at TEXT,
    last_video_id TEXT,
    analysis_delay_minutes INTEGER NOT NULL DEFAULT 90,
    max_clips_per_video INTEGER NOT NULL DEFAULT 3,
    min_duration_seconds INTEGER NOT NULL DEFAULT 20,
    target_duration_seconds INTEGER NOT NULL DEFAULT 38,
    max_duration_seconds INTEGER NOT NULL DEFAULT 60,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    youtube_video_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT,
    duration_seconds REAL,
    thumbnail_url TEXT NOT NULL DEFAULT '',
    local_path TEXT,
    transcript_path TEXT,
    category_id TEXT,
    discovered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id TEXT PRIMARY KEY,
    source_video_id INTEGER NOT NULL REFERENCES source_videos(id) ON DELETE CASCADE,
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

CREATE INDEX IF NOT EXISTS idx_jobs_due ON analysis_jobs(status, scheduled_at);

CREATE TABLE IF NOT EXISTS clip_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_video_id INTEGER NOT NULL REFERENCES source_videos(id) ON DELETE CASCADE,
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    local_score REAL NOT NULL,
    signals_json TEXT NOT NULL DEFAULT '{}',
    ai_score REAL,
    ai_reason TEXT NOT NULL DEFAULT '',
    refined_start_seconds REAL,
    refined_end_seconds REAL,
    status TEXT NOT NULL DEFAULT 'Detected',
    ai_title TEXT NOT NULL DEFAULT '',
    ai_description TEXT NOT NULL DEFAULT '',
    ai_tags_json TEXT NOT NULL DEFAULT '[]',
    reframe_mode TEXT NOT NULL DEFAULT 'auto',
    focus_x REAL NOT NULL DEFAULT 0.5,
    focus_y REAL NOT NULL DEFAULT 0.5,
    facecam_x REAL,
    facecam_y REAL,
    facecam_width REAL,
    facecam_height REAL,
    gameplay_x REAL,
    gameplay_y REAL,
    gameplay_width REAL,
    gameplay_height REAL,
    hud_x REAL,
    hud_y REAL,
    hud_width REAL,
    hud_height REAL
);

CREATE TABLE IF NOT EXISTS rendered_clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES clip_candidates(id) ON DELETE CASCADE,
    source_video_id INTEGER NOT NULL REFERENCES source_videos(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    format TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Ready',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    buffer_start_seconds REAL,
    buffer_end_seconds REAL
);

CREATE INDEX IF NOT EXISTS idx_clips_status ON rendered_clips(status, created_at DESC);

CREATE TABLE IF NOT EXISTS youtube_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL UNIQUE,
    channel_name TEXT NOT NULL,
    channel_url TEXT NOT NULL,
    avatar_url TEXT NOT NULL DEFAULT '',
    credential_key TEXT NOT NULL UNIQUE,
    connected_at TEXT NOT NULL,
    last_verified_at TEXT
);

CREATE TABLE IF NOT EXISTS publish_jobs (
    id TEXT PRIMARY KEY,
    rendered_clip_id INTEGER NOT NULL REFERENCES rendered_clips(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES youtube_accounts(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    category_id TEXT NOT NULL DEFAULT '20',
    privacy_status TEXT NOT NULL DEFAULT 'private',
    made_for_kids INTEGER NOT NULL DEFAULT 0,
    notify_subscribers INTEGER NOT NULL DEFAULT 0,
    scheduled_for TEXT,
    status TEXT NOT NULL DEFAULT 'Queued',
    progress REAL NOT NULL DEFAULT 0,
    remote_video_id TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(rendered_clip_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_publish_jobs_status ON publish_jobs(status, created_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage (
    usage_date TEXT PRIMARY KEY,
    videos_started INTEGER NOT NULL DEFAULT 0,
    source_minutes REAL NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_eur REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    job_id TEXT
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            row = connection.execute("SELECT version FROM schema_info LIMIT 1").fetchone()
            if row is None:
                connection.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] > SCHEMA_VERSION:
                raise RuntimeError("This database was created by a newer ClipRadar version.")
            elif row["version"] < SCHEMA_VERSION:
                self._migrate(connection, int(row["version"]))

    def _migrate(self, connection: sqlite3.Connection, version: int) -> None:
        if version < 2:
            existing = {row["name"] for row in connection.execute("PRAGMA table_info(clip_candidates)")}
            additions = {
                "ai_title": "TEXT NOT NULL DEFAULT ''",
                "ai_description": "TEXT NOT NULL DEFAULT ''",
                "ai_tags_json": "TEXT NOT NULL DEFAULT '[]'",
                "reframe_mode": "TEXT NOT NULL DEFAULT 'auto'",
                "focus_x": "REAL NOT NULL DEFAULT 0.5",
                "focus_y": "REAL NOT NULL DEFAULT 0.5",
                "facecam_x": "REAL",
                "facecam_y": "REAL",
                "facecam_width": "REAL",
                "facecam_height": "REAL",
            }
            for name, declaration in additions.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE clip_candidates ADD COLUMN {name} {declaration}")

            row = connection.execute("SELECT value_json FROM settings WHERE key = 'clips'").fetchone()
            if row:
                try:
                    payload = json.loads(row["value_json"])
                    payload["captions_enabled"] = False
                    payload["word_highlighting"] = False
                    connection.execute(
                        "UPDATE settings SET value_json = ? WHERE key = 'clips'",
                        (json.dumps(payload, separators=(",", ":")),),
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        if version < 3:
            video_columns = {row["name"] for row in connection.execute("PRAGMA table_info(source_videos)")}
            if "category_id" not in video_columns:
                connection.execute("ALTER TABLE source_videos ADD COLUMN category_id TEXT")
        if version < 4:
            candidate_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(clip_candidates)")
            }
            region_columns = (
                "gameplay_x", "gameplay_y", "gameplay_width", "gameplay_height",
                "hud_x", "hud_y", "hud_width", "hud_height",
            )
            for name in region_columns:
                if name not in candidate_columns:
                    connection.execute(f"ALTER TABLE clip_candidates ADD COLUMN {name} REAL")
            clip_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(rendered_clips)")
            }
            for name in ("buffer_start_seconds", "buffer_end_seconds"):
                if name not in clip_columns:
                    connection.execute(f"ALTER TABLE rendered_clips ADD COLUMN {name} REAL")
            connection.execute(
                """UPDATE rendered_clips
                   SET buffer_start_seconds = (
                           SELECT COALESCE(x.refined_start_seconds, x.start_seconds)
                           FROM clip_candidates x WHERE x.id = rendered_clips.candidate_id
                       ),
                       buffer_end_seconds = (
                           SELECT COALESCE(x.refined_end_seconds, x.end_seconds)
                           FROM clip_candidates x WHERE x.id = rendered_clips.candidate_id
                       )
                   WHERE status = 'Ready' AND buffer_start_seconds IS NULL"""
            )
        connection.execute("UPDATE schema_info SET version = ?", (SCHEMA_VERSION,))

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
