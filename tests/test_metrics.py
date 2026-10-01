"""Tests unitaires pour les métriques de pacing RTP et de lag de boucle (metrics.py)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from airplay_sender.metrics import LoopLagMonitor, LossStats, PacingStats, SessionMetrics


def test_pacing_stats_empty_window() -> None:
    assert PacingStats().snapshot_and_reset() == ""


def test_pacing_stats_buckets_and_reset() -> None:
    stats = PacingStats()
    # Intervalles: 8, 8, 0.5 (rafale), 25, 60 ms
    t = 100.0
    stats.record(t)
    for gap_ms in (8, 8, 0.5, 25, 60):
        t += gap_ms / 1000.0
        stats.record(t)

    summary = stats.snapshot_and_reset()
    assert "rafales(<2ms):1" in summary
    assert ">20ms:2" in summary
    assert ">50ms:1" in summary
    assert "max=60ms" in summary
    # La fenêtre est remise à zéro
    assert stats.snapshot_and_reset() == ""


def test_pacing_stats_warns_on_stall_rate_limited(caplog: pytest.LogCaptureFixture) -> None:
    stats = PacingStats(context_func=lambda: "buffer=42ms")
    with caplog.at_level(logging.WARNING, logger="airplay_sender.metrics"):
        stats.record(10.0)
        stats.record(10.1)  # trou de 100 ms -> alerte
        stats.record(10.2)  # 2e trou dans la même seconde -> limité
    warnings = [r for r in caplog.records if "Trou d'envoi" in r.getMessage()]
    assert len(warnings) == 1
    assert "buffer=42ms" in warnings[0].getMessage()


def test_loss_stats_counts_and_resets() -> None:
    stats = LossStats()
    stats.record(2, 10.0)
    stats.record(30, 10.1)
    assert stats.snapshot_and_reset() == "Retransmissions demandées: 2 (32 paquets)"
    assert stats.snapshot_and_reset() == "Retransmissions demandées: 0 (0 paquets)"


def test_session_metrics_summary_includes_extras() -> None:
    metrics = SessionMetrics()
    assert "Envoi RTP" not in metrics.format_summary()
    metrics.pacing_summary = "Envoi RTP: p50=8.0ms"
    metrics.loop_lag_summary = "Lag boucle: max=3ms"
    summary = metrics.format_summary()
    assert "Envoi RTP: p50=8.0ms" in summary
    assert "Lag boucle: max=3ms" in summary


async def test_loop_lag_monitor_detects_blocking() -> None:
    import time

    monitor = LoopLagMonitor()
    monitor.start()
    await asyncio.sleep(0.05)
    time.sleep(0.08)  # bloque volontairement la boucle
    await asyncio.sleep(0.05)
    monitor.stop()
    summary = monitor.snapshot_and_reset()
    assert summary.startswith("Lag boucle:")
    assert ">20ms:" in summary and ">20ms:0" not in summary
