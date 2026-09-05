from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipradar.models import (
    Channel,
    ClipCandidate,
    ClipStatus,
    PublishStatus,
    RenderedClip,
    SourceVideo,
)
from clipradar.publishing.service import PublishingService
from clipradar.publishing.youtube import (
    ConnectedChannel,
    UploadResult,
    YouTubePublishingClient,
    _safe_google_error,
)
from clipradar.settings.models import PublishingSettings


class FakeYouTubePublishingClient:
    def __init__(self):
        self.uploaded = None

    def validate_client_config(self, raw_json: str) -> dict:
        return json.loads(raw_json)

    def connect(self, _client_json: str) -> ConnectedChannel:
        return ConnectedChannel(
            channel_id="UC_PUBLISH",
            channel_name="Upload Test",
            channel_url="https://youtube.test/channel/UC_PUBLISH",
            avatar_url="",
            credentials_json='{"refresh_token":"unit-test-oauth-secret"}',
        )

    def verify(self, _credentials_json: str) -> tuple[str, str, str]:
        return "UC_PUBLISH", "Upload Test", '{"refresh_token":"verified-unit-test-secret"}'

    def upload(self, job, clip_path, _credentials_json, **callbacks):
        assert Path(clip_path).is_file()
        callbacks["save_session_uri"]("https://upload.test/private-session")
        callbacks["progress"](0.55)
        callbacks["save_credentials"]('{"refresh_token":"rotated-unit-test-secret"}')
        self.uploaded = job
        return UploadResult(
            "youtube-video-id",
            PublishStatus.SCHEDULED if job.scheduled_for else PublishStatus.PRIVATE,
            "private",
        )


def _ready_clip(services, tmp_path: Path) -> int:
    channel = services.repositories.channels.add(Channel(
        None, "UC_SOURCE", "Source", "", "https://youtube.test/source"
    ))
    source, _ = services.repositories.videos.upsert(SourceVideo(
        None, int(channel.id), "source-video", "Source video", "https://youtube.test/watch?v=source"
    ))
    candidate = services.repositories.candidates.replace_for_video(int(source.id), [
        ClipCandidate(
            None,
            int(source.id),
            1,
            12,
            90,
            {},
            ai_score=91,
            ai_title="Gemini made this title",
            ai_description="A relevant Gemini description.",
            ai_tags=["Minecraft", "reaction"],
        )
    ])[0]
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"test-media")
    clip = services.repositories.clips.add(RenderedClip(
        None, int(candidate.id), int(source.id), str(path), 11, "Vertical 9:16"
    ))
    return int(clip.id)


def test_queue_and_scheduled_upload_preserve_gemini_metadata_and_secrets(services, tmp_path: Path):
    fake = FakeYouTubePublishingClient()
    publishing = PublishingService(services.repositories, services.settings, fake)  # type: ignore[arg-type]
    services.settings.save_publishing(PublishingSettings(
        default_tags="ClipRadar, Gaming",
        max_uploads_per_day=10,
    ))
    services.settings.secrets.set_youtube_client('{"installed":{"client_id":"unit-test"}}')
    account = publishing.connect_account()
    assert publishing.verify_account(int(account.id)) == "Connected · Upload Test"
    assert "verified-unit-test-secret" in services.settings.secrets.get_youtube_token(account.credential_key)
    clip_id = _ready_clip(services, tmp_path)
    scheduled = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")

    job = publishing.queue_clip(
        clip_id=clip_id,
        account_id=int(account.id),
        title="Gemini made this title",
        description="A relevant Gemini description.",
        tags=["Minecraft", "reaction", "minecraft"],
        privacy_status="Public",
        scheduled_for=scheduled,
    )

    assert job.tags == ["Minecraft", "reaction", "ClipRadar", "Gaming"]
    assert services.repositories.clips.get(clip_id).status == ClipStatus.APPROVED
    assert services.settings.secrets.get_youtube_upload_session(job.id) is None
    video_id = publishing.upload(job.id)
    finished = services.repositories.publish.get(job.id)
    assert video_id == "youtube-video-id"
    assert finished.status == PublishStatus.SCHEDULED
    assert finished.progress == 1
    assert fake.uploaded.title == "Gemini made this title"
    database_bytes = services.paths.database.read_bytes()
    assert b"unit-test-oauth-secret" not in database_bytes
    assert b"rotated-unit-test-secret" not in database_bytes
    assert b"private-session" not in database_bytes


def test_cancel_returns_clip_to_review_and_allows_requeue(services, tmp_path: Path):
    fake = FakeYouTubePublishingClient()
    publishing = PublishingService(services.repositories, services.settings, fake)  # type: ignore[arg-type]
    services.settings.secrets.set_youtube_client("{}")
    account = publishing.connect_account()
    clip_id = _ready_clip(services, tmp_path)
    first = publishing.queue_clip(
        clip_id=clip_id,
        account_id=int(account.id),
        title="First title",
        description="",
        tags=[],
        privacy_status="Private",
        scheduled_for=None,
    )
    publishing.cancel(first.id)
    assert services.repositories.clips.get(clip_id).status == ClipStatus.READY
    second = publishing.queue_clip(
        clip_id=clip_id,
        account_id=int(account.id),
        title="Edited title",
        description="Edited description",
        tags=["edited"],
        privacy_status="Unlisted",
        scheduled_for=None,
    )
    assert second.id == first.id
    assert second.title == "Edited title"
    assert second.status == PublishStatus.QUEUED


def test_youtube_request_body_contains_generated_tags_and_schedule():
    from clipradar.models import PublishJob

    scheduled = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
    job = PublishJob(
        id="job",
        rendered_clip_id=1,
        account_id=1,
        title="Gemini title",
        description="Gemini description",
        tags=["Minecraft", "IShowSpeed"],
        category_id="20",
        privacy_status="public",
        made_for_kids=False,
        notify_subscribers=False,
        scheduled_for=scheduled,
    )
    body = YouTubePublishingClient._request_body(job)
    assert body["snippet"]["tags"] == ["Minecraft", "IShowSpeed"]
    assert body["snippet"]["title"] == "Gemini title"
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"].endswith("Z")


def test_google_oauth_client_must_be_a_desktop_app():
    client = YouTubePublishingClient()
    valid = {
        "installed": {
            "client_id": "unit.apps.googleusercontent.com",
            "client_secret": "not-a-real-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    assert client.validate_client_config(json.dumps(valid))["installed"]["client_id"].startswith("unit")
    with pytest.raises(Exception, match="Desktop"):
        client.validate_client_config(json.dumps({"web": valid["installed"]}))


def test_google_errors_never_echo_oauth_secrets():
    credentials = json.dumps({
        "token": "access-secret-value",
        "refresh_token": "refresh-secret-value",
        "client_secret": "client-secret-value",
    })
    message = _safe_google_error(
        RuntimeError("request access-secret-value refresh_token=refresh-secret-value client-secret-value failed"),
        credentials,
    )
    assert "secret-value" not in message
    assert "[redacted]" in message


def test_schedule_must_be_in_the_future(services, tmp_path: Path):
    fake = FakeYouTubePublishingClient()
    publishing = PublishingService(services.repositories, services.settings, fake)  # type: ignore[arg-type]
    services.settings.secrets.set_youtube_client("{}")
    account = publishing.connect_account()
    clip_id = _ready_clip(services, tmp_path)
    with pytest.raises(ValueError, match="future"):
        publishing.queue_clip(
            clip_id=clip_id,
            account_id=int(account.id),
            title="Title",
            description="",
            tags=[],
            privacy_status="Public",
            scheduled_for=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
