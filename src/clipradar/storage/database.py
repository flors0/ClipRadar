from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 1


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
    status TEXT NOT NULL DEFAULT 'Detected'
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
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clips_status ON rendered_clips(status, created_at DESC);

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

