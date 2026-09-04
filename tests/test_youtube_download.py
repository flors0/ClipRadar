from __future__ import annotations

from pathlib import Path

from clipradar.app.paths import AppPaths
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
