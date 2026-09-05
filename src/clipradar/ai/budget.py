from __future__ import annotations

from dataclasses import dataclass

from clipradar.settings.models import BudgetSettings
from clipradar.storage.repositories import UsageRepository


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    reason: str = ""


class BudgetGuard:
    def __init__(self, usage: UsageRepository):
        self.usage = usage

    def can_start_video(self, duration_seconds: float, limits: BudgetSettings) -> BudgetDecision:
        current = self.usage.get_today()
        minutes = max(0, duration_seconds) / 60
        if current["videos_started"] >= limits.max_videos_per_day:
            return BudgetDecision(
                False,
                f"Daily video analysis limit reached · {current['videos_started']}/{limits.max_videos_per_day} "
                "videos already started today.",
            )
        if current["source_minutes"] + minutes > limits.max_source_minutes_per_day:
            return BudgetDecision(
                False,
                f"Daily source-minute limit reached · {current['source_minutes']:.1f} used + "
                f"{minutes:.1f} for this video > {limits.max_source_minutes_per_day} minutes.",
            )
        if current["estimated_cost_eur"] >= limits.max_ai_cost_per_day_eur:
            return BudgetDecision(
                False,
                f"Daily AI cost limit reached · €{current['estimated_cost_eur']:.3f} / "
                f"€{limits.max_ai_cost_per_day_eur:.2f}.",
            )
        return BudgetDecision(True)

    def can_send_request(self, conservative_reserve_eur: float, limits: BudgetSettings) -> BudgetDecision:
        current = self.usage.get_today()
        if current["estimated_cost_eur"] + conservative_reserve_eur > limits.max_ai_cost_per_day_eur:
            return BudgetDecision(
                False,
                f"The next candidate could exceed the daily AI cost limit · €{current['estimated_cost_eur']:.3f} "
                f"used + up to €{conservative_reserve_eur:.3f} reserved > "
                f"€{limits.max_ai_cost_per_day_eur:.2f}.",
            )
        return BudgetDecision(True)
