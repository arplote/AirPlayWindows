"""Configuration pytest partagée."""

from __future__ import annotations

import pytest

from airplay_sender.netprobe import NetworkProbe


@pytest.fixture(autouse=True)
def _no_real_network_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Évite que les tests du superviseur envoient de vrais pings ICMP."""
    monkeypatch.setattr(NetworkProbe, "start", lambda self, speaker_ip: None)
