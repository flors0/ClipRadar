from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from clipradar.models import utc_now
from clipradar.settings.models import (
    AISettings,
    BudgetSettings,
    ClipSettings,
    GeneralSettings,
    MonitoringSettings,
    StorageSettings,
    merge_dataclass,
)
from clipradar.settings.secrets import SecretStore
from clipradar.storage.database import Database


class SettingsService:
    def __init__(self, database: Database, secrets: SecretStore):
        self.db = database
        self.secrets = secrets

    def _read(self, key: str) -> dict[str, Any] | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else None

    def _write(self, key: str, value: Any) -> None:
        payload = json.dumps(asdict(value), separators=(",", ":"))
        with self.db.connection() as connection:
            connection.execute(
                """INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at""",
                (key, payload, utc_now()),
            )

    def general(self) -> GeneralSettings:
        return merge_dataclass(GeneralSettings, self._read("general"))

    def monitoring(self) -> MonitoringSettings:
        return merge_dataclass(MonitoringSettings, self._read("monitoring"))

    def clips(self) -> ClipSettings:
        return merge_dataclass(ClipSettings, self._read("clips"))

    def ai(self) -> AISettings:
        return merge_dataclass(AISettings, self._read("ai"))

    def budget(self) -> BudgetSettings:
        return merge_dataclass(BudgetSettings, self._read("budget"))

    def storage(self) -> StorageSettings:
        return merge_dataclass(StorageSettings, self._read("storage"))

    def save_general(self, value: GeneralSettings) -> None:
        self._write("general", value)

    def save_monitoring(self, value: MonitoringSettings) -> None:
        self._write("monitoring", value)

    def save_clips(self, value: ClipSettings) -> None:
        if not value.minimum_duration <= value.target_duration <= value.maximum_duration:
            raise ValueError("Clip durations must satisfy minimum ≤ target ≤ maximum.")
        self._write("clips", value)

    def save_ai(self, value: AISettings) -> None:
        if not value.model.strip():
            raise ValueError("Select a Gemini model.")
        self._write("ai", value)

    def save_budget(self, value: BudgetSettings) -> None:
        if min(value.max_videos_per_day, value.max_source_minutes_per_day, value.max_clips_per_video) < 1:
            raise ValueError("Budget limits must be greater than zero.")
        if value.max_ai_cost_per_day_eur <= 0:
            raise ValueError("The daily AI cost limit must be greater than zero.")
        self._write("budget", value)

    def save_storage(self, value: StorageSettings) -> None:
        self._write("storage", value)

