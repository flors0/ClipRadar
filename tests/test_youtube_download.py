from __future__ import annotations

from pathlib import Path

import pytest

from clipradar.app.paths import AppPaths
from clipradar.models import OperationCancelled
from clipradar.youtube.client import RemoteVideo, YouTubeClient


def test_subtitle_rate_limit_does_not_discard_downloaded_video(tmp_path: Path, monkeypatch):
    paths = AppPaths.create(tmp_path)
    calls: list[dict] = []

    class FakeYDL:
        def __init__(self, options: dict):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url: str, download: bool):
            assert download is True
            calls.append(self.options)
            if self.options.get("skip_download"):
                raise RuntimeError("HTTP Error 429: Too Many Requests")
            output = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
            output.write_bytes(b"valid source media")
            return {
                "id": "rate-limit-video",
                "duration": 42,
                "automatic_captions": {
                    "de": [{"url": "https://example.test/de-captions"}],
                    "en": [{"url": "https://example.test/en-captions"}],
                },
            }

    monkeypatch.setattr(YouTubeClient, "_ydl", staticmethod(FakeYDL))
    downloaded = YouTubeClient(paths).download(RemoteVideo(
        video_id="rate-limit-video",
        title="Rate limit test",
        url="https://youtube.com/watch?v=rate-limit-video",
        channel_id="UC_TEST",
    ))

    assert downloaded.video_path.exists()
    assert downloaded.duration_seconds == 42
    assert downloaded.transcript_path is None
    assert len(calls) == 2
    assert "writesubtitles" not in calls[0]
    assert calls[1]["skip_download"] is True
    assert calls[1]["subtitleslangs"] == ["en"]
    assert YouTubeClient._preferred_caption_languages({"automatic_captions": {"de": []}}) == []


def test_http_403_retries_once_with_direct_stream_and_canonical_url(tmp_path: Path, monkeypatch):
    paths = AppPaths.create(tmp_path)
    calls: list[tuple[str, dict]] = []

    class FakeYDL:
        def __init__(self, options: dict):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url: str, download: bool):
            assert download is True
            calls.append((url, self.options))
            if len(calls) == 1:
                partial = Path(self.options["outtmpl"].replace("%(ext)s", "mp4.part"))
                partial.write_bytes(b"partial download")
                raise RuntimeError("unable to download video data: HTTP Error 403: Forbidden")
            output = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
            output.write_bytes(b"valid source media")
            return {"id": "replay-video", "duration": 81}

    monkeypatch.setattr(YouTubeClient, "_ydl", staticmethod(FakeYDL))
    downloaded = YouTubeClient(paths).download(RemoteVideo(
        video_id="replay-video",
        title="Finished stream replay",
        url="https://www.youtube.com/live/replay-video?feature=share",
        channel_id="UC_TEST",
    ))

    assert downloaded.video_path.read_bytes() == b"valid source media"
    assert len(calls) == 2
    assert {url for url, _options in calls} == {
        "https://www.youtube.com/watch?v=replay-video"
    }
    assert "[protocol^=http]" in calls[1][1]["format"]
    assert not list((paths.downloads / "replay-video").glob("*.part"))


def test_download_can_be_cancelled_before_network_work(tmp_path: Path, monkeypatch):
    paths = AppPaths.create(tmp_path)

    class UnexpectedYDL:
        def __init__(self, _options: dict):
            raise AssertionError("yt-dlp must not start after cancellation")

    monkeypatch.setattr(YouTubeClient, "_ydl", staticmethod(UnexpectedYDL))
    with pytest.raises(OperationCancelled):
        YouTubeClient(paths).download(
            RemoteVideo(
                video_id="cancel-video",
                title="Cancel test",
                url="https://youtube.com/watch?v=cancel-video",
                channel_id="UC_TEST",
            ),
            cancel_requested=lambda: True,
        )


def test_youtube_feed_supplies_flat_playlist_upload_timestamps(monkeypatch):
    xml = b"""<?xml version='1.0' encoding='UTF-8'?>
    <feed xmlns='http://www.w3.org/2005/Atom'
          xmlns:yt='http://www.youtube.com/xml/schemas/2015'>
      <entry>
        <yt:videoId>feed-video</yt:videoId>
        <published>2026-09-06T20:15:00+00:00</published>
      </entry>
    </feed>"""

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return xml

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())

    assert YouTubeClient._feed_publish_times("UC_FEED") == {
        "feed-video": "2026-09-06T20:15:00+00:00"
    }


def test_recent_videos_resolve_only_dates_missing_from_flat_playlist_and_feed(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[str] = []

    class FakeYDL:
        def __init__(self, options: dict):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url: str, download: bool):
            calls.append(url)
            if url.endswith("/videos"):
                return {
                    "channel_id": "UC_METADATA",
                    "entries": [
                        {"id": "dated", "title": "Dated", "timestamp": 1788724800},
                        {"id": "missing", "title": "Missing date", "view_count": 1200},
                    ],
                }
            assert download is False
            return {
                "id": "missing",
                "title": "Missing date",
                "channel_id": "UC_METADATA",
                "upload_date": "20260905",
                "view_count": 1300,
            }

    monkeypatch.setattr(YouTubeClient, "_ydl", staticmethod(FakeYDL))
    monkeypatch.setattr(YouTubeClient, "_feed_publish_times", staticmethod(lambda _channel: {}))
    client = YouTubeClient(AppPaths.create(tmp_path))

    videos = client.list_recent_videos("https://youtube.test/channel", limit=2)

    assert videos[0].published_at is not None
    assert videos[1].published_at.startswith("2026-09-05")
    assert len(calls) == 2
