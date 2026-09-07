from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from clipradar.app.paths import AppPaths, bundled_binary, resource_path
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
        demo_source = Path(temporary) / "demo-source.mp4"
        subprocess.run(
            [
                bundled_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i",
                "testsrc2=size=960x540:rate=12:duration=8",
                "-vf",
                "drawbox=x=25:y=25:w=190:h=145:color=0x334b57:t=fill,"
                "drawbox=x=740:y=35:w=175:h=120:color=0x554526:t=fill",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                str(demo_source),
            ],
            check=True,
        )
        source, _ = services.repositories.videos.upsert(SourceVideo(
            None,
            int(channel.id),
            "demo-video",
            "The Minecraft Save Nobody Expected",
            "https://youtube.com/watch?v=demo",
            duration_seconds=8,
            local_path=str(demo_source),
        ))
        candidates = services.repositories.candidates.replace_for_video(int(source.id), [
            ClipCandidate(
                None,
                int(source.id),
                0,
                8,
                93,
                {},
                ai_score=93,
                ai_reason="Strong reaction with a clear, self-contained payoff.",
                refined_start_seconds=1,
                refined_end_seconds=7,
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
                gameplay_x=0.0,
                gameplay_y=0.0,
                gameplay_width=1.0,
                gameplay_height=1.0,
                hud_x=0.77,
                hud_y=0.06,
                hud_width=0.19,
                hud_height=0.22,
                output_regions={
                    "facecam": (0.0, 0.0, 0.44, 0.27),
                    "hud": (0.44, 0.0, 0.56, 0.27),
                    "gameplay": (0.0, 0.27, 1.0, 0.73),
                },
            ),
            ClipCandidate(
                None,
                int(source.id),
                0,
                8,
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
        review_clip = services.repositories.clips.add(RenderedClip(
            None,
            int(candidates[0].id),
            int(source.id),
            str(clip_path),
            8,
            "Vertical 9:16",
            buffer_start_seconds=0,
            buffer_end_seconds=8,
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
                view_count=182_400,
            ),
            RemoteVideo(
                "demo-video-2",
                "This Strategy Looked Impossible Until It Worked",
                "https://youtube.com/watch?v=demo2",
                channel.channel_id,
                published_at="2026-09-05T18:10:00+00:00",
                view_count=96_200,
            ),
            RemoteVideo(
                "demo-video-3",
                "The Most Unexpected Ending of the Stream",
                "https://youtube.com/watch?v=demo3",
                channel.channel_id,
                published_at="2026-09-03T11:00:00+00:00",
                view_count=51_800,
            ),
        ])
        window.resize(1440, 900)
        window.show()
        app.processEvents()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(args.output))
        window._set_page(1)
        app.processEvents()
        window.grab().save(str(args.output.with_stem(f"{args.output.stem}-monitoring")))
        window._set_page(3)
        app.processEvents()
        window.grab().save(str(args.output.with_stem(f"{args.output.stem}-review")))
        window._open_clip_framing(int(review_clip.id))
        app.processEvents()
        window.grab().save(str(args.output.with_stem(f"{args.output.stem}-framing")))
        window._close_framing_setup()
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
