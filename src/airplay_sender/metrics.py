"""Métriques de performance et de stabilité du streaming AirPlay.

Phase 4 du projet AirPlayWindows.
Mesure les underruns, overflows, déconnexions, remplissage moyen du tampon et latence.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

_LOGGER = logging.getLogger("airplay_sender.metrics")


@dataclass
class SessionMetrics:
    """Métriques cumulées d'une session de streaming."""

    start_time: float = field(default_factory=time.monotonic)
    reconnect_count: int = 0
    total_underruns: int = 0
    total_overflows: int = 0
    total_silence_bytes: int = 0
    estimated_latency_ms: float = 2000.0  # Latence de tampon typique AirPlay (2.0s)
    buffer_levels_ms: list[float] = field(default_factory=list)

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def avg_buffer_level_ms(self) -> float:
        if not self.buffer_levels_ms:
            return 0.0
        return sum(self.buffer_levels_ms) / len(self.buffer_levels_ms)

    def record_buffer_level(self, level_ms: float) -> None:
        self.buffer_levels_ms.append(level_ms)
        if len(self.buffer_levels_ms) > 120:  # ~1 heure à 1 échantillon toutes les 30s
            self.buffer_levels_ms.pop(0)

    def format_summary(self) -> str:
        hours = int(self.uptime_seconds // 3600)
        minutes = int((self.uptime_seconds % 3600) // 60)
        secs = int(self.uptime_seconds % 60)
        uptime_str = f"{hours:02d}h {minutes:02d}m {secs:02d}s"

        return (
            f"[MÉTRIQUES] Uptime: {uptime_str} | "
            f"Buffer moyen: {self.avg_buffer_level_ms:.1f} ms | "
            f"Underruns: {self.total_underruns} | "
            f"Overflows: {self.total_overflows} | "
            f"Reconnexions: {self.reconnect_count} | "
            f"Latence est.: {self.estimated_latency_ms:.0f} ms"
        )


class MetricsLogger:
    """Tâche de journalisation périodique des métriques."""

    def __init__(
        self,
        get_metrics_func: Callable[[], SessionMetrics],
        interval_seconds: float = 30.0,
    ) -> None:
        self.get_metrics = get_metrics_func
        self.interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval)
                metrics = self.get_metrics()
                _LOGGER.info(metrics.format_summary())
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Erreur boucle métriques : %s", exc)

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
