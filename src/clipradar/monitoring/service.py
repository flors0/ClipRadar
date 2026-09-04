from __future__ import annotations

from clipradar.channels.service import ChannelService, ScanResult


class MonitoringService:
    """Thin domain façade kept independent from Qt scheduling."""

    def __init__(self, channels: ChannelService):
        self.channels = channels

    def check_now(self) -> ScanResult:
        return self.channels.scan_all()

