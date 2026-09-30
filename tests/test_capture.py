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


def test_find_loopback_device_prefers_virtual() -> None:
    """Vérifie que find_loopback_device choisit en priorité le câble virtuel."""
    from unittest.mock import patch

    from airplay_sender.capture import find_loopback_device

    fake_devices = [
        {
            "index": 0,
            "name": "Haut-parleurs (Realtek Audio)",
            "samplerate": 48000,
            "channels": 2,
            "is_default": True,
            "is_virtual": False,
        },
        {
            "index": 1,
            "name": "CABLE Input (VB-Audio Virtual Cable)",
            "samplerate": 44100,
            "channels": 2,
            "is_default": False,
            "is_virtual": True,
        },
    ]

    with patch("airplay_sender.capture.list_wasapi_loopback_devices", return_value=fake_devices):
        # prefer_virtual=True doit choisir le câble virtuel
        dev = find_loopback_device(prefer_virtual=True)
        assert dev is not None
        assert dev["name"] == "CABLE Input (VB-Audio Virtual Cable)"
        assert dev["is_virtual"] is True

        # prefer_virtual=False doit choisir le périphérique par défaut
        dev_default = find_loopback_device(prefer_virtual=False)
        assert dev_default is not None
        assert dev_default["name"] == "Haut-parleurs (Realtek Audio)"
        assert dev_default["is_default"] is True


def test_find_loopback_device_specific_name() -> None:
    """Vérifie la recherche d'un périphérique spécifique par nom ou index."""
    from unittest.mock import patch

    from airplay_sender.capture import find_loopback_device

    fake_devices = [
        {
            "index": 5,
            "name": "Casque Bluetooth (Hands-Free)",
            "samplerate": 16000,
            "channels": 1,
            "is_default": False,
            "is_virtual": False,
        },
    ]

    with patch("airplay_sender.capture.list_wasapi_loopback_devices", return_value=fake_devices):
        dev_by_idx = find_loopback_device(device_name_or_index=5)
        assert dev_by_idx is not None
        assert dev_by_idx["index"] == 5

        dev_by_name = find_loopback_device(device_name_or_index="Casque")
        assert dev_by_name is not None
        assert dev_by_name["index"] == 5
