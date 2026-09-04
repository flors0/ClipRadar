from __future__ import annotations

from dataclasses import dataclass

from clipradar.ai.budget import BudgetGuard
from clipradar.ai.gemini import GeminiClient
from clipradar.analysis.candidates import CandidateDetector
from clipradar.app.paths import AppPaths
from clipradar.channels.service import ChannelService
from clipradar.jobs.pipeline import AnalysisPipeline
from clipradar.monitoring.service import MonitoringService
from clipradar.rendering.renderer import ClipRenderer
from clipradar.review.service import ReviewService
from clipradar.settings.secrets import KeyringSecretStore, SecretStore
from clipradar.settings.service import SettingsService
from clipradar.storage.database import Database
from clipradar.storage.repositories import Repositories
from clipradar.youtube.client import YouTubeClient


@dataclass(slots=True)
class AppServices:
    paths: AppPaths
    database: Database
    repositories: Repositories
    settings: SettingsService
    youtube: YouTubeClient
    channels: ChannelService
    monitoring: MonitoringService
    gemini: GeminiClient
    pipeline: AnalysisPipeline
    review: ReviewService

    @classmethod
    def create(cls, paths: AppPaths, secret_store: SecretStore | None = None) -> "AppServices":
        database = Database(paths.database)
        database.initialize()
        repositories = Repositories(database)
        settings = SettingsService(database, secret_store or KeyringSecretStore())
        youtube = YouTubeClient(paths)
        channels = ChannelService(repositories, settings, youtube)
        monitoring = MonitoringService(channels)
        gemini = GeminiClient()
        pipeline = AnalysisPipeline(
            paths,
            repositories,
            settings,
            youtube,
            CandidateDetector(),
            gemini,
            BudgetGuard(repositories.usage),
            ClipRenderer(paths),
        )
        review = ReviewService(repositories, pipeline)
        repositories.jobs.recover_interrupted()
        repositories.clips.recover_regenerating()
        return cls(paths, database, repositories, settings, youtube, channels, monitoring, gemini, pipeline, review)

