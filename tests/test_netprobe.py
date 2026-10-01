"""Tests de la sonde réseau (netprobe.py) : statistiques et lecture des compteurs."""

from __future__ import annotations

import logging
import struct

import pytest

from airplay_sender.netprobe import (
    NetworkProbe,
    PingStats,
    WifiSample,
    WifiStats,
    _build_sample,
)


def test_ping_stats_window_and_loss() -> None:
    stats = PingStats("Box")
    for rtt in (2.0, 3.0, 4.0, None, 250.0):
        stats.record(rtt)

    window = stats.snapshot_and_reset()
    assert (window.sent, window.lost, window.slow) == (5, 1, 1)
    assert window.max_ms == 250.0
    assert window.degraded  # 20 % de perte
    assert stats.snapshot_and_reset().sent == 0


def test_ping_stats_not_degraded_when_clean() -> None:
    stats = PingStats("Enceinte")
    for rtt in (3.0, 4.0, 5.0, 6.0):
        stats.record(rtt)
    assert not stats.snapshot_and_reset().degraded


def test_ping_stats_warns_on_consecutive_losses_and_recovery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stats = PingStats("Enceinte")
    with caplog.at_level(logging.WARNING, logger="airplay_sender.netprobe"):
        for i in range(4):
            stats.record(None, now=100.0 + i)
        stats.record(5.0, now=104.0)
    messages = [r.getMessage() for r in caplog.records]
    assert any("pings perdus d'affilée" in m for m in messages)
    assert any("de nouveau joignable" in m for m in messages)


def _stats_payload(tx: int, failed: int, retry: int, multi: int) -> bytes:
    ulls = [0] * 28 + [0] * 16
    ulls[28], ulls[30], ulls[31], ulls[32] = tx, failed, retry, multi
    return struct.pack(f"<{len(ulls)}Q", *ulls)


def test_build_sample_parses_raw_wlan_answers() -> None:
    sample = _build_sample(
        struct.pack("<l", -57), struct.pack("<L", 100), _stats_payload(1000, 2, 150, 40)
    )
    assert sample == WifiSample(-57, 100, 1000, 150, 40, 2)
    assert sample.band == "5/6 GHz"


def test_wifi_stats_window_uses_counter_deltas() -> None:
    stats = WifiStats()
    stats.record(WifiSample(-50, 36, 1000, 100, 10, 0))
    assert stats.snapshot_and_reset() is not None  # fixe la référence

    stats.record(WifiSample(-60, 36, 1500, 200, 30, 0))
    stats.record(WifiSample(-55, 36, 2000, 300, 50, 1))
    window = stats.snapshot_and_reset()
    assert window is not None
    assert (window.tx, window.retries, window.failed) == (1000, 200, 1)
    assert window.rssi_min == -60
    assert window.degraded  # 20 % de retries


def test_wifi_stats_logs_channel_change(caplog: pytest.LogCaptureFixture) -> None:
    stats = WifiStats()
    with caplog.at_level(logging.WARNING, logger="airplay_sender.netprobe"):
        stats.record(WifiSample(-50, 11, 10, 0, 0, 0))
        stats.record(WifiSample(-50, 100, 20, 0, 0, 0))
    assert stats.total_channel_changes == 1
    assert any("changement de canal" in r.getMessage() for r in caplog.records)


def test_session_report_reading() -> None:
    probe = NetworkProbe()
    assert probe.session_report() == ""

    probe._windows, probe._box_degraded, probe._speaker_degraded, probe._both_degraded = 10, 3, 3, 3
    assert "PC <-> box" in probe.session_report()

    probe._box_degraded, probe._both_degraded = 0, 0
    assert "box <-> enceinte" in probe.session_report()

    probe._speaker_degraded = 0
    assert "aucune dégradation" in probe.session_report()
