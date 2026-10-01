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


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(len(sorted_values) * pct))
    return sorted_values[idx]


class PacingStats:
    """Mesure de la régularité d'envoi des paquets RTP (diagnostic des hachures audio).

    À 44.1 kHz avec 352 frames par paquet, l'intervalle nominal est ~7.98 ms.
    Les intervalles < 2 ms correspondent à des rafales de rattrapage après un retard.
    """

    STALL_WARN_MS = 50.0
    WARN_EVERY_SEC = 1.0

    def __init__(self, context_func: Callable[[], str] | None = None) -> None:
        self.context_func = context_func
        self._last: float | None = None
        self._last_warn = 0.0
        self._gaps_ms: list[float] = []

    def reset(self) -> None:
        self._last = None
        self._gaps_ms = []

    def record(self, now: float) -> None:
        """Enregistre l'envoi d'un paquet à l'instant `now` (secondes, perf_counter)."""
        last, self._last = self._last, now
        if last is None:
            return
        gap_ms = (now - last) * 1000.0
        self._gaps_ms.append(gap_ms)
        if gap_ms >= self.STALL_WARN_MS and now - self._last_warn >= self.WARN_EVERY_SEC:
            self._last_warn = now
            context = f" ({self.context_func()})" if self.context_func else ""
            _LOGGER.warning("Trou d'envoi RTP de %.0f ms%s", gap_ms, context)

    def snapshot_and_reset(self) -> str:
        """Résumé de la fenêtre écoulée, puis remise à zéro. Vide si aucun paquet."""
        gaps, self._gaps_ms = self._gaps_ms, []
        if not gaps:
            return ""
        ordered = sorted(gaps)
        return (
            f"Envoi RTP: p50={_percentile(ordered, 0.5):.1f}ms "
            f"p99={_percentile(ordered, 0.99):.1f}ms max={ordered[-1]:.0f}ms "
            f"rafales(<2ms):{sum(1 for g in gaps if g < 2.0)} "
            f">20ms:{sum(1 for g in gaps if g > 20.0)} "
            f">50ms:{sum(1 for g in gaps if g > 50.0)}"
        )


class LossStats:
    """Compte les demandes de retransmission émises par le récepteur (pertes réseau UDP)."""

    WARN_PACKETS = 20
    WARN_EVERY_SEC = 1.0

    def __init__(self) -> None:
        self._requests = 0
        self._packets = 0
        self._last_warn = 0.0

    def reset(self) -> None:
        self._requests = 0
        self._packets = 0

    def record(self, lost_packets: int, now: float) -> None:
        self._requests += 1
        self._packets += lost_packets
        if lost_packets >= self.WARN_PACKETS and now - self._last_warn >= self.WARN_EVERY_SEC:
            self._last_warn = now
            _LOGGER.warning("Le récepteur signale %d paquets RTP perdus d'un coup", lost_packets)

    def snapshot_and_reset(self) -> str:
        requests, packets = self._requests, self._packets
        self.reset()
        return f"Retransmissions demandées: {requests} ({packets} paquets)"


class LoopLagMonitor:
    """Mesure le retard de la boucle asyncio (proxy de la contention GIL / tâches bloquantes)."""

    INTERVAL_SEC = 0.010
    WARN_LAG_MS = 50.0
    WARN_EVERY_SEC = 1.0

    def __init__(self, context_func: Callable[[], str] | None = None) -> None:
        self.context_func = context_func
        self._lags_ms: list[float] = []
        self._last_warn = 0.0
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def _run_loop(self) -> None:
        while self._running:
            t0 = time.perf_counter()
            try:
                await asyncio.sleep(self.INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            now = time.perf_counter()
            lag_ms = max(0.0, (now - t0 - self.INTERVAL_SEC) * 1000.0)
            self._lags_ms.append(lag_ms)
            if lag_ms >= self.WARN_LAG_MS and now - self._last_warn >= self.WARN_EVERY_SEC:
                self._last_warn = now
                context = f" ({self.context_func()})" if self.context_func else ""
                _LOGGER.warning("Boucle asyncio bloquée %.0f ms%s", lag_ms, context)

    def snapshot_and_reset(self) -> str:
        lags, self._lags_ms = self._lags_ms, []
        if not lags:
            return ""
        ordered = sorted(lags)
        return (
            f"Lag boucle: p99={_percentile(ordered, 0.99):.1f}ms "
            f"max={ordered[-1]:.0f}ms >20ms:{sum(1 for v in lags if v > 20.0)}"
        )

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None


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
    pacing_summary: str = ""
    loop_lag_summary: str = ""
    loss_summary: str = ""
    network_summary: str = ""

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

        summary = (
            f"[MÉTRIQUES] Uptime: {uptime_str} | "
            f"Buffer moyen: {self.avg_buffer_level_ms:.1f} ms | "
            f"Underruns: {self.total_underruns} | "
            f"Overflows: {self.total_overflows} | "
            f"Reconnexions: {self.reconnect_count} | "
            f"Latence est.: {self.estimated_latency_ms:.0f} ms"
        )
        for extra in (self.pacing_summary, self.loop_lag_summary, self.loss_summary):
            if extra:
                summary += f" | {extra}"
        return summary


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
                if metrics.network_summary:
                    _LOGGER.info(metrics.network_summary)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Erreur boucle métriques : %s", exc)

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
