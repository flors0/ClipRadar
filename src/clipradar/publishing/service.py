from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from clipradar.models import ClipStatus, PublishJob, PublishStatus, YouTubeAccount, utc_now
from clipradar.publishing.youtube import YouTubePublishingClient, YouTubePublishingError
from clipradar.settings.service import SettingsService
from clipradar.storage.repositories import Repositories


ProgressCallback = Callable[[str, float], None]


class PublishingService:
    def __init__(
        self,
        repositories: Repositories,
        settings: SettingsService,
        client: YouTubePublishingClient | None = None,
    ):
        self.repos = repositories
        self.settings = settings
        self.client = client or YouTubePublishingClient()
        self._progress_milestones: dict[str, int] = {}

    def import_client_file(self, path: str | Path) -> str:
        path = Path(path)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise YouTubePublishingError(f"The OAuth client file could not be read: {exc}") from exc
        self.client.validate_client_config(raw)
        self.settings.secrets.set_youtube_client(raw)
        return "Google OAuth client imported"

    def client_configured(self) -> bool:
        return bool(self.settings.secrets.get_youtube_client())

    def connect_account(self) -> YouTubeAccount:
        client_json = self.settings.secrets.get_youtube_client()
        if not client_json:
            raise YouTubePublishingError("Import a Google Desktop OAuth client JSON first.")
        connected = self.client.connect(client_json)
        credential_key = connected.channel_id
        self.settings.secrets.set_youtube_token(credential_key, connected.credentials_json)
        account = self.repos.youtube_accounts.upsert(YouTubeAccount(
            id=None,
            channel_id=connected.channel_id,
            channel_name=connected.channel_name,
            channel_url=connected.channel_url,
            avatar_url=connected.avatar_url,
            credential_key=credential_key,
            last_verified_at=utc_now(),
        ))
        self.repos.activity.add(f"Connected YouTube channel {account.channel_name}", "success")
        return account

    def verify_account(self, account_id: int) -> str:
        account = self._account(account_id)
        token = self.settings.secrets.get_youtube_token(account.credential_key)
        if not token:
            raise YouTubePublishingError("The secure YouTube token is missing. Connect the channel again.")
        channel_id, channel_name, refreshed_token = self.client.verify(token)
        if channel_id != account.channel_id:
            raise YouTubePublishingError("Google returned a different YouTube channel. Connect the intended channel again.")
        self.settings.secrets.set_youtube_token(account.credential_key, refreshed_token)
        self.repos.youtube_accounts.mark_verified(account_id, channel_name)
        return f"Connected · {channel_name}"

    def disconnect_account(self, account_id: int) -> None:
        account = self._account(account_id)
        if self.repos.publish.has_active_for_account(account_id):
            raise YouTubePublishingError("Wait for active uploads to finish or cancel queued uploads first.")
        self.settings.secrets.delete_youtube_token(account.credential_key)
        self.repos.youtube_accounts.delete(account_id)
        self.repos.activity.add(f"Disconnected YouTube channel {account.channel_name}")

    def queue_clip(
        self,
        *,
        clip_id: int,
        account_id: int,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str,
        scheduled_for: str | None,
    ) -> PublishJob:
        clip = self.repos.clips.get(clip_id)
        if not clip or not Path(clip.file_path).is_file():
            raise FileNotFoundError("The rendered clip file is missing.")
        account = self._account(account_id)
        if not self.settings.secrets.get_youtube_token(account.credential_key):
            raise YouTubePublishingError("The selected YouTube channel must be connected again.")
        config = self.settings.publishing()
        if self.repos.publish.uploads_started_today() >= config.max_uploads_per_day:
            raise YouTubePublishingError("The configured daily YouTube upload limit has been reached.")
        clean_title = " ".join(title.replace("\n", " ").split()).strip()[:100]
        if not clean_title:
            raise ValueError("A YouTube title is required.")
        clean_description = description.replace("\r", "").strip()[:5000]
        clean_tags = _merge_tags(tags, config.default_tags)
        privacy = privacy_status.strip().lower()
        if privacy not in {"private", "unlisted", "public"}:
            raise ValueError("Select Private, Unlisted, or Public visibility.")
        normalized_schedule = _validate_schedule(scheduled_for)
        job = PublishJob(
            id=str(uuid.uuid4()),
            rendered_clip_id=clip_id,
            account_id=account_id,
            title=clean_title,
            description=clean_description,
            tags=clean_tags,
            category_id=config.category_id,
            privacy_status=privacy,
            made_for_kids=config.made_for_kids,
            notify_subscribers=config.notify_subscribers,
            scheduled_for=normalized_schedule,
        )
        saved = self.repos.publish.add(job)
        self.repos.clips.update_status(clip_id, ClipStatus.APPROVED)
        verb = "scheduled" if normalized_schedule else "queued"
        self.repos.activity.add(f"{clean_title} {verb} for YouTube", "success", saved.id)
        return saved

    def upload(self, job_id: str, progress: ProgressCallback | None = None) -> str:
        progress = progress or (lambda _stage, _value: None)
        job = self._job(job_id)
        account = self._account(job.account_id)
        clip = self.repos.clips.get(job.rendered_clip_id)
        if not clip:
            raise YouTubePublishingError("The clip record no longer exists.")
        token = self.settings.secrets.get_youtube_token(account.credential_key)
        if not token:
            raise YouTubePublishingError("The secure YouTube token is missing. Connect the channel again.")
        self.repos.publish.update(
            job.id, PublishStatus.UPLOADING, 0, increment_attempts=True
        )
        self._progress_milestones[job.id] = 0
        destination = f"scheduled for {job.scheduled_for}" if job.scheduled_for else job.privacy_status
        self.repos.activity.add(
            f"Upload attempt {job.attempts + 1} started · {destination} · {len(job.tags)} tags",
            "info",
            job.id,
        )
        try:
            result = self.client.upload(
                job,
                clip.file_path,
                token,
                progress=lambda value: self._progress(job.id, value, progress),
                save_credentials=lambda value: self.settings.secrets.set_youtube_token(account.credential_key, value),
                existing_session_uri=self.settings.secrets.get_youtube_upload_session(job.id),
                save_session_uri=lambda value: self.settings.secrets.set_youtube_upload_session(job.id, value),
            )
            self.repos.publish.update(
                job.id, result.status, 1, remote_video_id=result.video_id, error=None
            )
            self.settings.secrets.delete_youtube_upload_session(job.id)
            self._progress_milestones.pop(job.id, None)
            self.repos.activity.add(
                f"Uploaded {job.title} to {account.channel_name}", "success", job.id
            )
            return result.video_id
        except Exception as exc:
            current = self.repos.publish.get(job.id)
            self.repos.publish.update(
                job.id,
                PublishStatus.FAILED,
                current.progress if current else job.progress,
                error=str(exc)[:700],
            )
            self.repos.activity.add(f"YouTube upload failed: {str(exc)[:500]}", "error", job.id)
            self._progress_milestones.pop(job.id, None)
            raise

    def retry(self, job_id: str) -> None:
        job = self._job(job_id)
        if job.status != PublishStatus.FAILED:
            raise YouTubePublishingError("Only a failed upload can be retried.")
        self.repos.publish.retry(job_id)
        self.repos.activity.add("Upload queued for another attempt", "warning", job_id)

    def cancel(self, job_id: str) -> None:
        job = self._job(job_id)
        if job.status not in {PublishStatus.QUEUED, PublishStatus.FAILED}:
            raise YouTubePublishingError("Only a queued or failed upload can be cancelled.")
        self.repos.publish.cancel(job_id)
        self.repos.clips.update_status(job.rendered_clip_id, ClipStatus.READY)
        self.settings.secrets.delete_youtube_upload_session(job_id)
        self.repos.activity.add("Publishing job cancelled · clip returned to Review", "warning", job_id)

    def _progress(self, job_id: str, value: float, callback: ProgressCallback) -> None:
        self.repos.publish.update(job_id, PublishStatus.UPLOADING, value)
        milestone = min(75, int(max(0.0, value) * 4) * 25)
        previous = self._progress_milestones.get(job_id, 0)
        if milestone >= 25 and milestone > previous:
            self._progress_milestones[job_id] = milestone
            self.repos.activity.add(f"YouTube upload reached {milestone}%", "info", job_id)
        callback("Uploading to YouTube", value)

    def _account(self, account_id: int) -> YouTubeAccount:
        account = self.repos.youtube_accounts.get(account_id)
        if not account:
            raise YouTubePublishingError("The selected YouTube channel no longer exists.")
        return account

    def _job(self, job_id: str) -> PublishJob:
        job = self.repos.publish.get(job_id)
        if not job:
            raise YouTubePublishingError("The publishing job no longer exists.")
        return job


def _validate_schedule(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Choose a valid publication date and time.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed < datetime.now(timezone.utc) + timedelta(minutes=2):
        raise ValueError("Scheduled publication must be at least two minutes in the future.")
    return parsed.isoformat(timespec="seconds")


def _merge_tags(generated: list[str], defaults: str) -> list[str]:
    values = [*generated, *defaults.split(",")]
    result: list[str] = []
    seen: set[str] = set()
    total = 0
    for raw in values:
        tag = " ".join(str(raw).replace("#", " ").split()).strip(" ,")[:60]
        key = tag.casefold()
        if not tag or key in seen:
            continue
        next_total = total + len(tag) + (1 if result else 0)
        if next_total > 450 or len(result) >= 15:
            break
        result.append(tag)
        seen.add(key)
        total = next_total
    return result
