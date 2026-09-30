"""Tests unitaires pour la capture audio loopback WASAPI."""

from __future__ import annotations

import time

from airplay_sender.capture import LoopbackCapture


def test_loopback_capture_initialization() -> None:
    """Vérifie que la capture s'initialise correctement avec les paramètres du système."""
    cap = LoopbackCapture()
    assert cap.samplerate > 0
    assert cap.channels in (1, 2, 6, 8)
    assert cap.dtype == "int16"
    assert len(cap.device_name) > 0
    assert not cap.is_running
    assert cap.overflow_count == 0


def test_loopback_capture_lifecycle() -> None:
    """Vérifie le démarrage, la lecture sans blocage et l'arrêt propre."""
    with LoopbackCapture() as cap:
        assert cap.is_running
        # Sur silence, read() doit renvoyer None ou un bloc de bytes sans lever d'exception
        data = cap.read(timeout=0.05)
        assert data is None or isinstance(data, bytes)
        time.sleep(0.1)

    assert not cap.is_running
