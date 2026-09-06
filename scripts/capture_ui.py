from __future__ import annotations

import argparse
import os
import sys
import tempfile
import uuid
from pathlib import Path

if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from clipradar.app.paths import AppPaths, resource_path
from clipradar.app.services import AppServices
from clipradar.models import (
    Channel,
    ClipCandidate,
    ClipStatus,
    JobStatus,
    PublishJob,
    RenderedClip,
    SourceVideo,
    YouTubeAccount,
    utc_now,
)
from clipradar.settings.secrets import MemorySecretStore
from clipradar.ui.main_window import MainWindow
from clipradar.ui.pages.review import PublishDialog
from clipradar.youtube.client import RemoteVideo


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(resource_path("resources/theme.qss").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="clipradar-ui-") as temporary:
        services = AppServices.create(AppPaths.create(temporary), MemorySecretStore())
        services.repositories.channels.add(Channel(
            None, "UC_DEMO", "Creator Channel", "", "https://youtube.com/@creator",
            last_checked_at="2026-09-04T14:30:00+00:00", last_video_id="demo",
        ))
        channel = services.repositories.channels.list_all()[0]
        source, _ = services.repositories.videos.upsert(SourceVideo(
            None,
            int(channel.id),
            "demo-video",
            "The Minecraft Save Nobody Expected",
            "https://youtube.com/watch?v=demo",
        ))
        candidates = services.repositories.candidates.replace_for_video(int(source.id), [
            ClipCandidate(
                None,
                int(source.id),
                125,
                160,
                93,
                {},
                ai_score=93,
                ai_reason="Strong reaction with a clear, self-contained payoff.",
                refined_start_seconds=127,
                refined_end_seconds=158,
                ai_title="He Somehow Survived This Minecraft Fall",
                ai_description=(
                    "A split-second Minecraft decision turns into an unbelievable save — and the reaction says everything.\n\n"
                    "Watch the full moment and see how close it really was."
                ),
                ai_tags=["Minecraft", "gaming reaction", "clutch save", "funny gaming", "YouTube Shorts"],
                reframe_mode="gaming_split",
                focus_x=0.64,
                focus_y=0.54,
                facecam_x=0.02,
                facecam_y=0.04,
                facecam_width=0.18,
                facecam_height=0.24,
            ),
            ClipCandidate(
                None,
                int(source.id),
                220,
                250,
                86,
                {},
                ai_score=86,
                ai_title="The Timing Could Not Have Been Better",
                ai_description="A perfectly timed gaming reaction.",
                ai_tags=["gaming", "reaction", "shorts"],
                reframe_mode="focus",
                focus_x=0.7,
            ),
        ])
        clip_path = Path(temporary) / "demo-clip.mp4"
        clip_path.write_bytes(b"preview-placeholder")
        services.repositories.clips.add(RenderedClip(
            None,
            int(candidates[0].id),
            int(source.id),
            str(clip_path),
            91,
            "Vertical 9:16",
            buffer_start_seconds=97,
            buffer_end_seconds=188,
        ))
        queued_clip = services.repositories.clips.add(RenderedClip(
            None,
            int(candidates[1].id),
            int(source.id),
            str(clip_path),
            30,
            "Vertical 9:16",
            ClipStatus.APPROVED,
        ))
        account = services.repositories.youtube_accounts.upsert(YouTubeAccount(
            None,
            "UC_UPLOAD_DEMO",
            "ClipRadar Shorts",
            "https://youtube.com/channel/UC_UPLOAD_DEMO",
            credential_key="UC_UPLOAD_DEMO",
        ))
        services.settings.secrets.set_youtube_client("demo-client")
        services.settings.secrets.set_youtube_token("UC_UPLOAD_DEMO", "demo-token")
        services.repositories.publish.add(PublishJob(
            id=str(uuid.uuid4()),
            rendered_clip_id=int(queued_clip.id),
            account_id=int(account.id),
            title="The Timing Could Not Have Been Better",
            description="A perfectly timed gaming reaction.",
            tags=["gaming", "reaction", "shorts"],
            category_id="20",
            privacy_status="private",
            made_for_kids=False,
            notify_subscribers=False,
            scheduled_for="2026-09-06T16:30:00+00:00",
        ))
        demo_job = services.repositories.jobs.create(int(source.id), utc_now(), manual=True)
        services.repositories.jobs.update(
            demo_job.id, JobStatus.ANALYZING, "Gemini ranking candidate 1/2", 0.46,
            increment_attempts=True,
        )
        services.repositories.activity.add("Analysis attempt 1 started", "info", demo_job.id)
        services.repositories.activity.add(
            "Local analysis found 2 candidates · model gemini-3.8-flash", "info", demo_job.id
        )
        services.repositories.activity.add(
            "Candidate 1/2 scored 93/100 · selected for rendering", "success", demo_job.id
        )
        services.repositories.activity.add("Monitoring started")
        services.repositories.activity.add("Added channel Creator Channel", "success")
        window = MainWindow(services, start_background=False)
        window.dashboard.set_videos(int(channel.id), [
            RemoteVideo(
                "demo-video",
                "The Minecraft Save Nobody Expected",
                "https://youtube.com/watch?v=demo",
                channel.channel_id,
                published_at="2026-09-06T13:20:00+00:00",
            ),
            RemoteVideo(
                "demo-video-2",
                "This Strategy Looked Impossible Until It Worked",
                "https://youtube.com/watch?v=demo2",
                channel.channel_id,
                published_at="2026-09-05T18:10:00+00:00",
            ),
            RemoteVideo(
                "demo-video-3",
                "The Most Unexpected Ending of the Stream",
                "https://youtube.com/watch?v=demo3",
                channel.channel_id,
                published_at="2026-09-03T11:00:00+00:00",
            ),
        ])
        window.resize(1440, 900)
        window.show()
        app.processEvents()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(args.output))
        window._set_page(3)
        app.processEvents()
        window.grab().save(str(args.output.with_stem(f"{args.output.stem}-review")))
        publish_dialog = PublishDialog(window.review.current, services.publishing, window)
        publish_dialog.show()
        app.processEvents()
        publish_dialog.grab().save(str(args.output.with_stem(f"{args.output.stem}-publish-dialog")))
        publish_dialog.close()
        window._set_page(4)
        app.processEvents()
        window.grab().save(str(args.output.with_stem(f"{args.output.stem}-publishing")))
        window._set_page(5)
        window.settings.nav.setCurrentRow(5)
        app.processEvents()
        window.grab().save(str(args.output.with_stem(f"{args.output.stem}-settings")))
        window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
