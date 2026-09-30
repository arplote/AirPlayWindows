"""Objets factices (fakes/mocks) pour les tests unitaires sans matériel physique."""

from __future__ import annotations

import asyncio
from typing import Any

from pyatv.const import Protocol

from airplay_sender.audio_pipeline import AudioPipeline


class FakeDeviceConfig:
    """Fausse configuration d'appareil pour les tests."""

    def __init__(self, name: str = "Fake Speaker", address: str = "192.168.1.100") -> None:
        self.name = name
        self.address = address
        self.identifier = "00:11:22:33:44:55"
        self.all_identifiers = [self.identifier]

    def set_credentials(self, protocol: Any, credentials: str) -> None:
        pass


class FakeStreamer:
    """Faux streamer AirPlay configurable pour simuler des pannes et reconnexions."""

    def __init__(self, fail_connect_count: int = 0, fail_stream_count: int = 0) -> None:
        self.fail_connect_count = fail_connect_count
        self.fail_stream_count = fail_stream_count
        self.connect_attempts = 0
        self.stream_attempts = 0
        self.is_connected = False
        self.is_streaming = False
        self.volume_level = 30.0

    @property
    def volume(self) -> float:
        return self.volume_level

    async def connect(
        self, device: Any, credentials: str | None = None, protocol: Protocol = Protocol.RAOP
    ) -> None:
        self.connect_attempts += 1
        if self.connect_attempts <= self.fail_connect_count:
            raise ConnectionError(f"Simulated connect failure {self.connect_attempts}")
        self.is_connected = True

    async def stream(self, pipeline: AudioPipeline) -> None:
        self.stream_attempts += 1
        self.is_streaming = True
        if self.stream_attempts <= self.fail_stream_count:
            self.is_streaming = False
            raise RuntimeError(f"Simulated stream drop {self.stream_attempts}")
        while self.is_streaming:
            await asyncio.sleep(0.01)

    async def set_volume(self, level: float) -> None:
        self.volume_level = level

    async def close(self) -> None:
        self.is_streaming = False
        self.is_connected = False
