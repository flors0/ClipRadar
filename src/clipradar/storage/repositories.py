from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable

from clipradar.models import (
    AnalysisJob,
    Channel,
    ClipCandidate,
    ClipStatus,
    JobStatus,
    PublishJob,
    PublishStatus,
    RenderedClip,
    SourceVideo,
    YouTubeAccount,
    utc_now,
)
from clipradar.storage.database import Database


def _channel(row: Any) -> Channel:
    data = dict(row)
    data["monitoring_enabled"] = bool(data["monitoring_enabled"])
    return Channel(**data)


def _video(row: Any) -> SourceVideo:
    return SourceVideo(**dict(row))


def _job(row: Any) -> AnalysisJob:
    data = dict(row)
    data["status"] = JobStatus(data["status"])
    data["manual"] = bool(data["manual"])
    return AnalysisJob(**data)


def _candidate(row: Any) -> ClipCandidate:
    data = dict(row)
    data["signals"] = json.loads(data.pop("signals_json") or "{}")
    data["ai_tags"] = json.loads(data.pop("ai_tags_json") or "[]")
    return ClipCandidate(**data)


def _clip(row: Any) -> RenderedClip:
    data = dict(row)
    data["status"] = ClipStatus(data["status"])
    return RenderedClip(**data)


def _youtube_account(row: Any) -> YouTubeAccount:
    return YouTubeAccount(**dict(row))


def _publish_job(row: Any) -> PublishJob:
    data = dict(row)
    data["tags"] = json.loads(data.pop("tags_json") or "[]")
    data["made_for_kids"] = bool(data["made_for_kids"])
    data["notify_subscribers"] = bool(data["notify_subscribers"])
    data["status"] = PublishStatus(data["status"])
    return PublishJob(**data)


class ChannelRepository:
    def __init__(self, database: Database):
        self.db = database

    def add(self, channel: Channel) -> Channel:
        values = asdict(channel)
        values.pop("id")
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        with self.db.connection() as connection:
            cursor = connection.execute(
                f"INSERT INTO channels ({columns}) VALUES ({placeholders})",
                values,
            )
            row = connection.execute("SELECT * FROM channels WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _channel(row)

    def list_all(self) -> list[Channel]:
        with self.db.connection() as connection:
            rows = connection.execute("SELECT * FROM channels ORDER BY name COLLATE NOCASE").fetchall()
        return [_channel(row) for row in rows]

    def get(self, channel_id: int) -> Channel | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        return _channel(row) if row else None

    def get_by_youtube_id(self, youtube_channel_id: str) -> Channel | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM channels WHERE channel_id = ?", (youtube_channel_id,)).fetchone()
        return _channel(row) if row else None

    def update(self, channel_id: int, **changes: Any) -> None:
        allowed = {
            "name", "avatar_url", "url", "monitoring_enabled", "last_checked_at", "last_video_id",
            "analysis_delay_minutes", "max_clips_per_video", "min_duration_seconds",
            "target_duration_seconds", "max_duration_seconds",
        }
        changes = {key: value for key, value in changes.items() if key in allowed}
        if not changes:
            return
        changes["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = :{key}" for key in changes)
        changes["id"] = channel_id
        with self.db.connection() as connection:
            connection.execute(f"UPDATE channels SET {assignments} WHERE id = :id", changes)

    def delete(self, channel_id: int) -> None:
        with self.db.connection() as connection:
            connection.execute("DELETE FROM channels WHERE id = ?", (channel_id,))


class VideoRepository:
    def __init__(self, database: Database):
        self.db = database

    def upsert(self, video: SourceVideo) -> tuple[SourceVideo, bool]:
        with self.db.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM source_videos WHERE youtube_video_id = ?", (video.youtube_video_id,)
            ).fetchone()
            if existing:
                connection.execute(
                    """UPDATE source_videos SET title = ?, url = ?, published_at = COALESCE(?, published_at),
                       duration_seconds = COALESCE(?, duration_seconds), thumbnail_url = COALESCE(NULLIF(?, ''), thumbnail_url)
                       WHERE id = ?""",
                    (video.title, video.url, video.published_at, video.duration_seconds, video.thumbnail_url, existing["id"]),
                )
                row = connection.execute("SELECT * FROM source_videos WHERE id = ?", (existing["id"],)).fetchone()
                return _video(row), False
            values = asdict(video)
            values.pop("id")
            columns = ", ".join(values)
            placeholders = ", ".join(f":{name}" for name in values)
            cursor = connection.execute(f"INSERT INTO source_videos ({columns}) VALUES ({placeholders})", values)
            row = connection.execute("SELECT * FROM source_videos WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _video(row), True

    def get(self, video_id: int) -> SourceVideo | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM source_videos WHERE id = ?", (video_id,)).fetchone()
        return _video(row) if row else None

    def get_by_youtube_id(self, youtube_video_id: str) -> SourceVideo | None:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM source_videos WHERE youtube_video_id = ?", (youtube_video_id,)
            ).fetchone()
        return _video(row) if row else None

    def latest_for_channel(self, channel_id: int) -> SourceVideo | None:
        with self.db.connection() as connection:
            row = connection.execute(
                """SELECT * FROM source_videos WHERE channel_id = ?
                   ORDER BY COALESCE(published_at, discovered_at) DESC LIMIT 1""",
                (channel_id,),
            ).fetchone()
        return _video(row) if row else None

    def set_media(self, video_id: int, local_path: str, transcript_path: str | None, duration: float) -> None:
        with self.db.connection() as connection:
            connection.execute(
                "UPDATE source_videos SET local_path = ?, transcript_path = ?, duration_seconds = ? WHERE id = ?",
                (local_path, transcript_path, duration, video_id),
            )


class JobRepository:
    ACTIVE = (JobStatus.WAITING.value, JobStatus.SCHEDULED.value, JobStatus.DOWNLOADING.value,
              JobStatus.ANALYZING.value, JobStatus.RENDERING.value)

    def __init__(self, database: Database):
        self.db = database

    def create(self, source_video_id: int, scheduled_at: str, manual: bool = False) -> AnalysisJob:
        job = AnalysisJob(
            id=str(uuid.uuid4()),
            source_video_id=source_video_id,
            status=JobStatus.WAITING if manual else JobStatus.SCHEDULED,
            stage="Queued",
            scheduled_at=scheduled_at,
            manual=manual,
        )
        values = asdict(job)
        values["status"] = job.status.value
        values["manual"] = int(job.manual)
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        with self.db.connection() as connection:
            connection.execute(f"INSERT INTO analysis_jobs ({columns}) VALUES ({placeholders})", values)
        return job

    def has_active_for_video(self, source_video_id: int) -> bool:
        placeholders = ",".join("?" for _ in self.ACTIVE)
        with self.db.connection() as connection:
            row = connection.execute(
                f"SELECT 1 FROM analysis_jobs WHERE source_video_id = ? AND status IN ({placeholders}) LIMIT 1",
                (source_video_id, *self.ACTIVE),
            ).fetchone()
        return row is not None

    def next_due(self, now: str | None = None) -> AnalysisJob | None:
        now = now or utc_now()
        with self.db.connection() as connection:
            row = connection.execute(
                """SELECT * FROM analysis_jobs
                   WHERE status IN (?, ?) AND scheduled_at <= ?
                   ORDER BY manual DESC, scheduled_at, created_at LIMIT 1""",
                (JobStatus.WAITING.value, JobStatus.SCHEDULED.value, now),
            ).fetchone()
        return _job(row) if row else None

    def get(self, job_id: str) -> AnalysisJob | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row) if row else None

    def update(
        self,
        job_id: str,
        status: JobStatus,
        stage: str,
        progress: float,
        error: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        with self.db.connection() as connection:
            connection.execute(
                """UPDATE analysis_jobs SET status = ?, stage = ?, progress = ?, error = ?,
                   attempts = attempts + ?, updated_at = ? WHERE id = ?""",
                (status.value, stage, max(0.0, min(1.0, progress)), error, int(increment_attempts), utc_now(), job_id),
            )

    def recover_interrupted(self) -> int:
        interrupted = (JobStatus.DOWNLOADING.value, JobStatus.ANALYZING.value, JobStatus.RENDERING.value)
        with self.db.connection() as connection:
            cursor = connection.execute(
                """UPDATE analysis_jobs SET status = ?, stage = 'Recovered after restart', progress = 0,
                   error = NULL, updated_at = ? WHERE status IN (?, ?, ?)""",
                (JobStatus.WAITING.value, utc_now(), *interrupted),
            )
        return cursor.rowcount

    def recent(self, limit: int = 8) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = connection.execute(
                """SELECT j.*, v.title AS video_title, c.name AS channel_name
                   FROM analysis_jobs j JOIN source_videos v ON v.id = j.source_video_id
                   JOIN channels c ON c.id = v.channel_id
                   ORDER BY j.updated_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        result = {status.value: 0 for status in JobStatus}
        with self.db.connection() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM analysis_jobs GROUP BY status").fetchall()
        result.update({row["status"]: row["count"] for row in rows})
        return result


class CandidateRepository:
    def __init__(self, database: Database):
        self.db = database

    def replace_for_video(self, source_video_id: int, candidates: Iterable[ClipCandidate]) -> list[ClipCandidate]:
        saved: list[ClipCandidate] = []
        with self.db.connection() as connection:
            connection.execute(
                """DELETE FROM clip_candidates WHERE source_video_id = ?
                   AND id NOT IN (SELECT candidate_id FROM rendered_clips)""",
                (source_video_id,),
            )
            for candidate in candidates:
                cursor = connection.execute(
                    """INSERT INTO clip_candidates
                       (source_video_id, start_seconds, end_seconds, local_score, signals_json, ai_score,
                        ai_reason, refined_start_seconds, refined_end_seconds, status, ai_title,
                        ai_description, ai_tags_json, reframe_mode, focus_x, focus_y, facecam_x,
                        facecam_y, facecam_width, facecam_height)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_video_id, candidate.start_seconds, candidate.end_seconds, candidate.local_score,
                        json.dumps(candidate.signals, separators=(",", ":")), candidate.ai_score,
                        candidate.ai_reason, candidate.refined_start_seconds, candidate.refined_end_seconds,
                        candidate.status, candidate.ai_title, candidate.ai_description,
                        json.dumps(candidate.ai_tags, separators=(",", ":")), candidate.reframe_mode,
                        candidate.focus_x, candidate.focus_y, candidate.facecam_x, candidate.facecam_y,
                        candidate.facecam_width, candidate.facecam_height,
                    ),
                )
                candidate.id = cursor.lastrowid
                saved.append(candidate)
        return saved

    def list_for_video(self, source_video_id: int) -> list[ClipCandidate]:
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM clip_candidates WHERE source_video_id = ? ORDER BY local_score DESC", (source_video_id,)
            ).fetchall()
        return [_candidate(row) for row in rows]

    def get(self, candidate_id: int) -> ClipCandidate | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM clip_candidates WHERE id = ?", (candidate_id,)).fetchone()
        return _candidate(row) if row else None

    def apply_ai_result(
        self,
        candidate_id: int,
        score: float,
        reason: str,
        refined_start: float,
        refined_end: float,
        *,
        title: str = "",
        description: str = "",
        tags: list[str] | None = None,
        reframe_mode: str = "auto",
        focus_x: float = 0.5,
        focus_y: float = 0.5,
        facecam_x: float | None = None,
        facecam_y: float | None = None,
        facecam_width: float | None = None,
        facecam_height: float | None = None,
    ) -> None:
        with self.db.connection() as connection:
            connection.execute(
                """UPDATE clip_candidates SET ai_score = ?, ai_reason = ?, refined_start_seconds = ?,
                   refined_end_seconds = ?, ai_title = ?, ai_description = ?, ai_tags_json = ?,
                   reframe_mode = ?, focus_x = ?, focus_y = ?, facecam_x = ?, facecam_y = ?,
                   facecam_width = ?, facecam_height = ?, status = 'Ranked' WHERE id = ?""",
                (
                    score, reason, refined_start, refined_end, title, description,
                    json.dumps(tags or [], separators=(",", ":")), reframe_mode, focus_x, focus_y,
                    facecam_x, facecam_y, facecam_width, facecam_height, candidate_id,
                ),
            )

    def mark_rendered(self, candidate_id: int) -> None:
        with self.db.connection() as connection:
            connection.execute("UPDATE clip_candidates SET status = 'Rendered' WHERE id = ?", (candidate_id,))

    def update_reframe_mode(self, candidate_id: int, mode: str) -> None:
        if mode not in {"auto", "focus", "gaming_split", "center", "contain"}:
            raise ValueError("Select a valid reframe mode.")
        with self.db.connection() as connection:
            connection.execute(
                "UPDATE clip_candidates SET reframe_mode = ? WHERE id = ?",
                (mode, candidate_id),
            )


class ClipRepository:
    def __init__(self, database: Database):
        self.db = database

    def add(self, clip: RenderedClip) -> RenderedClip:
        values = asdict(clip)
        values.pop("id")
        values["status"] = clip.status.value
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        with self.db.connection() as connection:
            cursor = connection.execute(f"INSERT INTO rendered_clips ({columns}) VALUES ({placeholders})", values)
            row = connection.execute("SELECT * FROM rendered_clips WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _clip(row)

    def get(self, clip_id: int) -> RenderedClip | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM rendered_clips WHERE id = ?", (clip_id,)).fetchone()
        return _clip(row) if row else None

    def update_status(self, clip_id: int, status: ClipStatus) -> None:
        with self.db.connection() as connection:
            connection.execute(
                "UPDATE rendered_clips SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, utc_now(), clip_id),
            )

    def list_review(self, include_decided: bool = False) -> list[dict[str, Any]]:
        where = "" if include_decided else "WHERE r.status IN ('Ready', 'Regenerating')"
        with self.db.connection() as connection:
            rows = connection.execute(
                f"""SELECT r.*, x.start_seconds, x.end_seconds, x.refined_start_seconds,
                    x.refined_end_seconds, x.ai_score, x.ai_reason, x.ai_title, x.ai_description,
                    x.ai_tags_json, x.reframe_mode, x.focus_x, x.focus_y, x.facecam_x,
                    x.facecam_y, x.facecam_width, x.facecam_height, v.title AS video_title,
                    v.url AS source_url, c.name AS channel_name
                    FROM rendered_clips r
                    JOIN clip_candidates x ON x.id = r.candidate_id
                    JOIN source_videos v ON v.id = r.source_video_id
                    JOIN channels c ON c.id = v.channel_id
                    {where} ORDER BY r.created_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def ready_count(self) -> int:
        with self.db.connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM rendered_clips WHERE status = 'Ready'").fetchone()
        return int(row["count"])

    def recover_regenerating(self) -> int:
        with self.db.connection() as connection:
            cursor = connection.execute(
                "UPDATE rendered_clips SET status = 'Ready', updated_at = ? WHERE status = 'Regenerating'",
                (utc_now(),),
            )
        return cursor.rowcount


class YouTubeAccountRepository:
    def __init__(self, database: Database):
        self.db = database

    def upsert(self, account: YouTubeAccount) -> YouTubeAccount:
        with self.db.connection() as connection:
            connection.execute(
                """INSERT INTO youtube_accounts
                   (channel_id, channel_name, channel_url, avatar_url, credential_key, connected_at, last_verified_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(channel_id) DO UPDATE SET
                     channel_name = excluded.channel_name,
                     channel_url = excluded.channel_url,
                     avatar_url = excluded.avatar_url,
                     credential_key = excluded.credential_key,
                     last_verified_at = excluded.last_verified_at""",
                (
                    account.channel_id, account.channel_name, account.channel_url, account.avatar_url,
                    account.credential_key, account.connected_at, account.last_verified_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM youtube_accounts WHERE channel_id = ?", (account.channel_id,)
            ).fetchone()
        return _youtube_account(row)

    def list_all(self) -> list[YouTubeAccount]:
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM youtube_accounts ORDER BY channel_name COLLATE NOCASE"
            ).fetchall()
        return [_youtube_account(row) for row in rows]

    def get(self, account_id: int) -> YouTubeAccount | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM youtube_accounts WHERE id = ?", (account_id,)).fetchone()
        return _youtube_account(row) if row else None

    def mark_verified(self, account_id: int, channel_name: str) -> None:
        with self.db.connection() as connection:
            connection.execute(
                "UPDATE youtube_accounts SET channel_name = ?, last_verified_at = ? WHERE id = ?",
                (channel_name, utc_now(), account_id),
            )

    def delete(self, account_id: int) -> None:
        with self.db.connection() as connection:
            connection.execute("DELETE FROM youtube_accounts WHERE id = ?", (account_id,))


class PublishRepository:
    ACTIVE = (PublishStatus.QUEUED.value, PublishStatus.UPLOADING.value)

    def __init__(self, database: Database):
        self.db = database

    def add(self, job: PublishJob) -> PublishJob:
        values = asdict(job)
        values["tags_json"] = json.dumps(values.pop("tags"), separators=(",", ":"))
        values["status"] = job.status.value
        values["made_for_kids"] = int(job.made_for_kids)
        values["notify_subscribers"] = int(job.notify_subscribers)
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        with self.db.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM publish_jobs WHERE rendered_clip_id = ? AND account_id = ?",
                (job.rendered_clip_id, job.account_id),
            ).fetchone()
            if existing:
                if existing["status"] != PublishStatus.CANCELLED.value:
                    raise ValueError("This clip already has a publishing job for that YouTube channel.")
                values["id"] = existing["id"]
                assignments = ", ".join(f"{name} = :{name}" for name in values if name != "id")
                connection.execute(
                    f"UPDATE publish_jobs SET {assignments} WHERE id = :id",
                    values,
                )
            else:
                connection.execute(f"INSERT INTO publish_jobs ({columns}) VALUES ({placeholders})", values)
            row = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (values["id"],)).fetchone()
        return _publish_job(row)

    def get(self, job_id: str) -> PublishJob | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
        return _publish_job(row) if row else None

    def next_queued(self) -> PublishJob | None:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM publish_jobs WHERE status = ? ORDER BY created_at LIMIT 1",
                (PublishStatus.QUEUED.value,),
            ).fetchone()
        return _publish_job(row) if row else None

    def list_all(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = connection.execute(
                """SELECT p.*, a.channel_name AS account_name, a.channel_id AS account_channel_id,
                   r.file_path, v.title AS source_title, c.name AS source_channel
                   FROM publish_jobs p
                   JOIN youtube_accounts a ON a.id = p.account_id
                   JOIN rendered_clips r ON r.id = p.rendered_clip_id
                   JOIN source_videos v ON v.id = r.source_video_id
                   JOIN channels c ON c.id = v.channel_id
                   ORDER BY p.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def uploads_started_today(self) -> int:
        day = datetime.now(timezone.utc).date().isoformat()
        with self.db.connection() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS count FROM publish_jobs
                   WHERE substr(created_at, 1, 10) = ? AND status != ?""",
                (day, PublishStatus.CANCELLED.value),
            ).fetchone()
        return int(row["count"])

    def has_active_for_account(self, account_id: int) -> bool:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM publish_jobs WHERE account_id = ? AND status IN (?, ?) LIMIT 1",
                (account_id, *self.ACTIVE),
            ).fetchone()
        return row is not None

    def update(
        self,
        job_id: str,
        status: PublishStatus,
        progress: float,
        *,
        remote_video_id: str | None = None,
        error: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        with self.db.connection() as connection:
            connection.execute(
                """UPDATE publish_jobs SET status = ?, progress = ?,
                   remote_video_id = COALESCE(?, remote_video_id),
                   error = ?,
                   attempts = attempts + ?, updated_at = ? WHERE id = ?""",
                (
                    status.value, max(0.0, min(1.0, progress)), remote_video_id,
                    error, int(increment_attempts), utc_now(), job_id,
                ),
            )

    def cancel(self, job_id: str) -> None:
        with self.db.connection() as connection:
            connection.execute(
                """UPDATE publish_jobs SET status = ?, error = NULL, updated_at = ?
                   WHERE id = ? AND status IN (?, ?)""",
                (
                    PublishStatus.CANCELLED.value, utc_now(), job_id,
                    PublishStatus.QUEUED.value, PublishStatus.FAILED.value,
                ),
            )

    def retry(self, job_id: str) -> None:
        with self.db.connection() as connection:
            connection.execute(
                """UPDATE publish_jobs SET status = ?, progress = 0, error = NULL, updated_at = ?
                   WHERE id = ? AND status = ?""",
                (PublishStatus.QUEUED.value, utc_now(), job_id, PublishStatus.FAILED.value),
            )

    def recover_interrupted(self) -> int:
        with self.db.connection() as connection:
            cursor = connection.execute(
                """UPDATE publish_jobs SET status = ?, progress = 0, error = NULL, updated_at = ?
                   WHERE status = ?""",
                (PublishStatus.QUEUED.value, utc_now(), PublishStatus.UPLOADING.value),
            )
        return cursor.rowcount


class UsageRepository:
    def __init__(self, database: Database):
        self.db = database

    @staticmethod
    def today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def get_today(self) -> dict[str, Any]:
        day = self.today()
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM ai_usage WHERE usage_date = ?", (day,)).fetchone()
        return dict(row) if row else {
            "usage_date": day, "videos_started": 0, "source_minutes": 0.0, "requests": 0,
            "input_tokens": 0, "output_tokens": 0, "estimated_cost_eur": 0.0,
        }

    def add(
        self, *, videos: int = 0, source_minutes: float = 0, requests: int = 0,
        input_tokens: int = 0, output_tokens: int = 0, estimated_cost_eur: float = 0,
    ) -> None:
        day = self.today()
        with self.db.connection() as connection:
            connection.execute(
                """INSERT INTO ai_usage
                   (usage_date, videos_started, source_minutes, requests, input_tokens, output_tokens, estimated_cost_eur)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(usage_date) DO UPDATE SET
                     videos_started = videos_started + excluded.videos_started,
                     source_minutes = source_minutes + excluded.source_minutes,
                     requests = requests + excluded.requests,
                     input_tokens = input_tokens + excluded.input_tokens,
                     output_tokens = output_tokens + excluded.output_tokens,
                     estimated_cost_eur = estimated_cost_eur + excluded.estimated_cost_eur""",
                (day, videos, source_minutes, requests, input_tokens, output_tokens, estimated_cost_eur),
            )


class ActivityRepository:
    def __init__(self, database: Database):
        self.db = database

    def add(self, message: str, level: str = "info", job_id: str | None = None) -> None:
        with self.db.connection() as connection:
            connection.execute(
                "INSERT INTO activity(created_at, level, message, job_id) VALUES (?, ?, ?, ?)",
                (utc_now(), level, message, job_id),
            )

    def recent(self, limit: int = 12) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = connection.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]


class Repositories:
    def __init__(self, database: Database):
        self.channels = ChannelRepository(database)
        self.videos = VideoRepository(database)
        self.jobs = JobRepository(database)
        self.candidates = CandidateRepository(database)
        self.clips = ClipRepository(database)
        self.youtube_accounts = YouTubeAccountRepository(database)
        self.publish = PublishRepository(database)
        self.usage = UsageRepository(database)
        self.activity = ActivityRepository(database)
