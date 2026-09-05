from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from clipradar.models import PublishJob, PublishStatus


YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_SCOPES = [YOUTUBE_UPLOAD_SCOPE, YOUTUBE_READ_SCOPE]
ProgressCallback = Callable[[float], None]
SecretCallback = Callable[[str], None]


class YouTubePublishingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ConnectedChannel:
    channel_id: str
    channel_name: str
    channel_url: str
    avatar_url: str
    credentials_json: str


@dataclass(frozen=True, slots=True)
class UploadResult:
    video_id: str
    status: PublishStatus
    privacy_status: str


class YouTubePublishingClient:
    def validate_client_config(self, raw_json: str) -> dict:
        try:
            payload = json.loads(raw_json)
            installed = payload["installed"]
            client_id = str(installed["client_id"])
            client_secret = str(installed["client_secret"])
            auth_uri = str(installed["auth_uri"])
            token_uri = str(installed["token_uri"])
            redirect_uris = [str(value) for value in installed["redirect_uris"]]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise YouTubePublishingError(
                "That file is not a Google OAuth client JSON for a Desktop app."
            ) from exc
        if not client_id.endswith(".apps.googleusercontent.com"):
            raise YouTubePublishingError("The OAuth client ID in that file is invalid.")
        if not client_secret:
            raise YouTubePublishingError("The OAuth client secret in that file is missing.")
        if not auth_uri.startswith("https://") or not token_uri.startswith("https://"):
            raise YouTubePublishingError("The OAuth endpoints in that file are invalid.")
        if not any(uri.startswith(("http://localhost", "http://127.0.0.1")) for uri in redirect_uris):
            raise YouTubePublishingError("The OAuth client must be created as a Google Desktop app.")
        return payload

    def connect(self, client_json: str) -> ConnectedChannel:
        config = self.validate_client_config(client_json)
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build

            flow = InstalledAppFlow.from_client_config(config, scopes=YOUTUBE_SCOPES)
            credentials = flow.run_local_server(
                host="127.0.0.1",
                port=0,
                open_browser=True,
                authorization_prompt_message="Opening Google sign-in in your browser…",
                success_message="YouTube is connected. You can close this browser tab and return to ClipRadar.",
            )
            youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
            response = youtube.channels().list(part="id,snippet", mine=True, maxResults=50).execute()
        except Exception as exc:
            raise YouTubePublishingError(_safe_google_error(exc, client_json)) from exc
        channels = response.get("items") or []
        if not channels:
            raise YouTubePublishingError(
                "Google authorized the account, but no YouTube channel was available. Create or select a channel and connect again."
            )
        channel = channels[0]
        snippet = channel.get("snippet") or {}
        thumbnails = snippet.get("thumbnails") or {}
        avatar = (thumbnails.get("default") or thumbnails.get("medium") or {}).get("url", "")
        channel_id = str(channel.get("id") or "")
        if not channel_id:
            raise YouTubePublishingError("YouTube returned an account without a channel ID.")
        return ConnectedChannel(
            channel_id=channel_id,
            channel_name=str(snippet.get("title") or "YouTube channel"),
            channel_url=f"https://www.youtube.com/channel/{channel_id}",
            avatar_url=str(avatar),
            credentials_json=credentials.to_json(),
        )

    def verify(self, credentials_json: str) -> tuple[str, str, str]:
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            credentials = Credentials.from_authorized_user_info(
                json.loads(credentials_json), scopes=YOUTUBE_SCOPES
            )
            youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
            response = youtube.channels().list(part="id,snippet", mine=True, maxResults=1).execute()
            channel = (response.get("items") or [None])[0]
            if not channel:
                raise YouTubePublishingError("The connected Google account no longer exposes a YouTube channel.")
            return (
                str(channel["id"]),
                str((channel.get("snippet") or {}).get("title") or "YouTube channel"),
                credentials.to_json(),
            )
        except YouTubePublishingError:
            raise
        except Exception as exc:
            raise YouTubePublishingError(_safe_google_error(exc, credentials_json)) from exc

    def upload(
        self,
        job: PublishJob,
        clip_path: str | Path,
        credentials_json: str,
        *,
        progress: ProgressCallback,
        save_credentials: SecretCallback,
        existing_session_uri: str | None = None,
        save_session_uri: SecretCallback | None = None,
    ) -> UploadResult:
        clip_path = Path(clip_path)
        if not clip_path.is_file():
            raise YouTubePublishingError("The rendered clip file no longer exists.")
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload

            credentials = Credentials.from_authorized_user_info(
                json.loads(credentials_json), scopes=YOUTUBE_SCOPES
            )
            youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
            body = self._request_body(job)
            media = MediaFileUpload(
                str(clip_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True
            )
            request = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
                notifySubscribers=job.notify_subscribers,
            )
            if existing_session_uri:
                request.resumable_uri = existing_session_uri
            response = None
            last_session = existing_session_uri
            while response is None:
                status, response = request.next_chunk()
                session_uri = getattr(request, "resumable_uri", None)
                if session_uri and session_uri != last_session and save_session_uri:
                    save_session_uri(str(session_uri))
                    last_session = str(session_uri)
                if status:
                    progress(max(0.0, min(0.99, float(status.progress()))))
            save_credentials(credentials.to_json())
        except Exception as exc:
            raise YouTubePublishingError(_safe_google_error(exc, credentials_json)) from exc

        video_id = str((response or {}).get("id") or "")
        if not video_id:
            raise YouTubePublishingError("YouTube accepted the upload but returned no video ID.")
        returned_privacy = str(((response or {}).get("status") or {}).get("privacyStatus") or "private").lower()
        if job.scheduled_for:
            status = PublishStatus.SCHEDULED
        elif returned_privacy == "private":
            status = PublishStatus.PRIVATE
        else:
            status = PublishStatus.PUBLISHED
        progress(1.0)
        return UploadResult(video_id, status, returned_privacy)

    @staticmethod
    def _request_body(job: PublishJob) -> dict:
        snippet: dict[str, object] = {
            "title": job.title,
            "description": job.description,
            "categoryId": job.category_id,
        }
        if job.tags:
            snippet["tags"] = job.tags
        status: dict[str, object] = {
            "privacyStatus": job.privacy_status,
            "selfDeclaredMadeForKids": job.made_for_kids,
            "containsSyntheticMedia": False,
        }
        if job.scheduled_for:
            scheduled = datetime.fromisoformat(job.scheduled_for.replace("Z", "+00:00"))
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            status["privacyStatus"] = "private"
            status["publishAt"] = scheduled.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return {"snippet": snippet, "status": status}


def _safe_google_error(exc: Exception, *secret_payloads: str) -> str:
    text = str(exc)
    for secret in _secret_values(*secret_payloads):
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)(access_token|refresh_token|client_secret)=([^&\s]+)", r"\1=[redacted]", text)
    lowered = text.lower()
    if "access_denied" in lowered or "denied" in lowered:
        return "YouTube connection was cancelled or denied."
    if "invalid_grant" in lowered or "token has been expired" in lowered or "revoked" in lowered:
        return "The YouTube connection expired or was revoked. Disconnect it and connect again."
    if "redirect_uri_mismatch" in lowered:
        return "The Google OAuth client is not configured as a Desktop app."
    if "quota" in lowered or "dailylimitexceeded" in lowered:
        return "The YouTube API upload limit has been reached for today."
    if "uploadlimitexceeded" in lowered:
        return "This YouTube channel has reached its current upload limit."
    if "invalidpublishat" in lowered:
        return "YouTube rejected the scheduled publication time. Choose a later time."
    if "invalidtitle" in lowered:
        return "YouTube rejected the title. Keep it non-empty and under 100 characters."
    if "invalidtags" in lowered:
        return "YouTube rejected the generated tags. Edit the tags and retry."
    if "timed out" in lowered or "connection" in lowered:
        return "The YouTube connection failed. Check the network and retry."
    return f"YouTube publishing failed: {text[:420]}"


def _secret_values(*payloads: str) -> list[str]:
    sensitive_keys = {"token", "access_token", "refresh_token", "client_secret", "client_id"}
    values: list[str] = []

    def collect(value: object, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                collect(child, str(child_key).lower())
        elif isinstance(value, list):
            for child in value:
                collect(child, key)
        elif key in sensitive_keys and isinstance(value, str) and len(value) >= 6:
            values.append(value)

    for payload in payloads:
        try:
            collect(json.loads(payload))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return sorted(set(values), key=len, reverse=True)
