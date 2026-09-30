"""Client de streaming AirPlay/RAOP adaptateur pour le pipeline audio temps réel.

Phase 4 du projet AirPlayWindows.
Permet de diffuser en continu le flux capturé vers l'enceinte Devialet sans fichier intermédiaire.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import pyatv
import pyatv.protocols.raop as raop
from pyatv.const import Protocol
from pyatv.interface import AppleTV, BaseConfig, MediaMetadata
from pyatv.protocols.raop import audio_source
from pyatv.protocols.raop.audio_source import AudioSource
from pyatv.support.metadata import EMPTY_METADATA

from airplay_sender.audio_pipeline import AudioPipeline

_LOGGER = logging.getLogger("airplay_sender.streamer")

# Patch transparent de pyatv pour accepter un AudioSource personnalisé dans stream_file
_orig_open_source = audio_source.open_source


async def _patched_open_source(
    source: Any, *args: Any, **kwargs: Any
) -> AudioSource:
    if isinstance(source, AudioSource):
        return source
    return await _orig_open_source(source, *args, **kwargs)


audio_source.open_source = _patched_open_source
raop.open_source = _patched_open_source


class PipelineAudioSource(AudioSource):
    """Source audio AirPlay connectée au ring buffer d'un AudioPipeline."""

    def __init__(self, pipeline: AudioPipeline, sample_rate: int = 44100) -> None:
        self.pipeline = pipeline
        self._sample_rate = sample_rate
        self._channels = 2
        self._sample_size = 2  # 16-bit
        self._is_stopped = False

    async def readframes(self, nframes: int) -> bytes:
        """Lit nframes audio (généralement 352 frames = 1408 octets à 44.1 kHz)."""
        if self._is_stopped:
            return b""

        nbytes = nframes * self._channels * self._sample_size
        # pull() renvoie toujours nbytes (produit du silence si le système est muet)
        return self.pipeline.pull(nbytes)

    async def get_metadata(self) -> MediaMetadata:
        return EMPTY_METADATA

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def sample_size(self) -> int:
        return self._sample_size

    @property
    def duration(self) -> int:
        # Source temps réel infinie
        return 0

    def stop(self) -> None:
        """Arrête la production de frames pour terminer la session proprement."""
        self._is_stopped = True


class AirPlayStreamer:
    """Gestionnaire de session de streaming vers un récepteur AirPlay/RAOP."""

    def __init__(self) -> None:
        self._atv: AppleTV | None = None
        self._current_source: PipelineAudioSource | None = None
        self._is_connected = False
        self._is_streaming = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def is_streaming(self) -> bool:
        return self._is_streaming

    @property
    def volume(self) -> float:
        if self._atv and hasattr(self._atv.audio, "volume"):
            return float(self._atv.audio.volume)
        return 0.0

    async def connect(
        self,
        device: BaseConfig,
        credentials: str | None = None,
        protocol: Protocol = Protocol.RAOP,
    ) -> None:
        """Établit la connexion avec le récepteur AirPlay/RAOP."""
        if self._is_connected:
            await self.close()

        loop = asyncio.get_running_loop()
        if credentials:
            device.set_credentials(protocol, credentials)

        _LOGGER.info(
            "Connexion à '%s' (%s) via %s...",
            device.name,
            device.address,
            protocol.name,
        )
        self._atv = await pyatv.connect(device, loop, protocol=protocol)
        self._is_connected = True
        _LOGGER.info("Connecté avec succès à %s.", device.name)

    async def stream(self, pipeline: AudioPipeline) -> None:
        """Diffuse en continu le flux audio fourni par le pipeline.

        Ne se termine que sur appel à close() ou sur erreur réseau/protocole.
        """
        if not self._atv or not self._is_connected:
            raise RuntimeError("Le streamer n'est pas connecté à un appareil AirPlay.")

        self._current_source = PipelineAudioSource(pipeline, sample_rate=pipeline.out_rate)
        self._is_streaming = True
        _LOGGER.info("Démarrage du flux AirPlay en direct...")

        try:
            await self._atv.stream.stream_file(cast(Any, self._current_source))
        except Exception as exc:
            _LOGGER.error("Erreur durant le streaming AirPlay : %s", exc)
            raise
        finally:
            self._is_streaming = False
            self._current_source = None
            _LOGGER.info("Flux AirPlay arrêté.")

    async def set_volume(self, level: float) -> None:
        """Règle le niveau de volume (0.0 à 100.0)."""
        if self._atv:
            clamped = max(0.0, min(100.0, level))
            await self._atv.audio.set_volume(clamped)

    async def close(self) -> None:
        """Ferme la session de streaming et libère les ressources réseau."""
        self._is_streaming = False
        if self._current_source:
            self._current_source.stop()
            self._current_source = None

        if self._atv:
            try:
                self._atv.close()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Erreur à la fermeture d'AppleTV : %s", exc)
            self._atv = None

        self._is_connected = False
        _LOGGER.info("Session de streaming fermée.")
