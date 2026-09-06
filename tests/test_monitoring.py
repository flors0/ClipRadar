from __future__ import annotations

from clipradar.channels.service import ChannelService
from clipradar.models import Channel, SourceVideo, utc_now
from clipradar.youtube.client import RemoteVideo, ResolvedChannel


class FakeYouTube:
    def __init__(self):
        self.items = [self.video("v1")]

    @staticmethod
    def video(video_id: str) -> RemoteVideo:
        return RemoteVideo(
            video_id=video_id,
            title=f"Video {video_id}",
            url=f"https://youtube.com/watch?v={video_id}",
            channel_id="UC_FAKE",
            duration_seconds=90,
        )

    def resolve_channel(self, _identifier: str) -> ResolvedChannel:
        return ResolvedChannel("UC_FAKE", "Fake Channel", "https://youtube.com/channel/UC_FAKE", latest_video_id="v1")

    def list_recent_videos(self, _url: str, limit: int = 12):
        return self.items[:limit]

    def resolve_video(self, _url: str) -> RemoteVideo:
        return self.video("specific-video")


def test_add_uses_baseline_then_monitoring_queues_only_new_uploads(services):
    youtube = FakeYouTube()
    channels = ChannelService(services.repositories, services.settings, youtube)
    channel = channels.add_channel("@fake")
    assert channel.last_video_id == "v1"
    assert services.repositories.jobs.next_due() is None
    youtube.items = [youtube.video("v3"), youtube.video("v2"), youtube.video("v1")]
    result = channels.scan_all()
    assert result.videos_discovered == 2
    assert result.jobs_scheduled == 2
    assert services.repositories.channels.get(int(channel.id)).last_video_id == "v3"


def test_manual_latest_is_queued_immediately(services):
    youtube = FakeYouTube()
    channels = ChannelService(services.repositories, services.settings, youtube)
    channel = channels.add_channel("@fake")
    job_id = channels.analyze_latest(int(channel.id))
    job = services.repositories.jobs.get(job_id)
    assert job is not None
    assert job.manual is True
    assert services.repositories.jobs.next_due() is not None


def test_manual_specific_video_stores_selected_genre(services):
    youtube = FakeYouTube()
    channels = ChannelService(services.repositories, services.settings, youtube)
    channel = channels.add_channel("@fake")

    job_id = channels.analyze_specific(
        int(channel.id), "https://youtube.test/watch?v=specific-video", "24"
    )

    job = services.repositories.jobs.get(job_id)
    source = services.repositories.videos.get(job.source_video_id)
    assert source.category_id == "24"


def test_analysis_queue_has_deterministic_sequential_positions(services):
    channel = services.repositories.channels.add(Channel(
        None, "UC_QUEUE", "Queue Channel", "", "https://youtube.test/queue"
    ))
    first_source, _ = services.repositories.videos.upsert(SourceVideo(
        None, int(channel.id), "queue-first", "First queued video", "https://youtube.test/first"
    ))
    second_source, _ = services.repositories.videos.upsert(SourceVideo(
        None, int(channel.id), "queue-second", "Second queued video", "https://youtube.test/second"
    ))
    scheduled = utc_now()
    first = services.repositories.jobs.create(int(first_source.id), scheduled)
    second = services.repositories.jobs.create(int(second_source.id), scheduled)

    assert services.repositories.jobs.queue_position(first.id) == 1
    assert services.repositories.jobs.queue_position(second.id) == 2
    assert services.repositories.jobs.next_due().id == first.id
