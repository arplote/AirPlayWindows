"""Client de streaming AirPlay/RAOP adaptateur pour le pipeline audio temps réel.

Phase 4 du projet AirPlayWindows.
Permet de diffuser en continu le flux capturé vers l'enceinte Devialet sans fichier intermédiaire.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, cast

import pyatv
import pyatv.protocols.raop as raop
from pyatv.const import Protocol
from pyatv.interface import AppleTV, BaseConfig, MediaMetadata
from pyatv.protocols.raop import audio_source
from pyatv.protocols.raop.audio_source import AudioSource, _to_audio_samples
from pyatv.protocols.raop.protocols import StreamContext
from pyatv.protocols.raop.stream_client import ControlClient, StreamClient
from pyatv.support.metadata import EMPTY_METADATA

from airplay_sender.audio_pipeline import AudioPipeline
from airplay_sender.metrics import LossStats, PacingStats

_LOGGER = logging.getLogger("airplay_sender.streamer")

# Configuration de la latence RAOP (en ms et frames à 44.1 kHz)
DEFAULT_RAOP_LATENCY_MS = 250
DEFAULT_RAOP_LATENCY_FRAMES = int(DEFAULT_RAOP_LATENCY_MS * 44.1)

# Patch de StreamContext pour forcer la faible latence (250 ms au lieu de 1500 ms)
_orig_stream_context_reset = StreamContext.reset


def _patched_stream_context_reset(self: StreamContext) -> None:
    _orig_stream_context_reset(self)
    self.latency = getattr(self, "custom_latency_frames", DEFAULT_RAOP_LATENCY_FRAMES)


StreamContext.reset = _patched_stream_context_reset  # type: ignore[method-assign]


def set_raop_latency_ms(latency_ms: int) -> None:
    """Modifie la latence matérielle AirPlay/RAOP négociée avec le récepteur."""
    global DEFAULT_RAOP_LATENCY_MS, DEFAULT_RAOP_LATENCY_FRAMES
    DEFAULT_RAOP_LATENCY_MS = max(100, latency_ms)
    DEFAULT_RAOP_LATENCY_FRAMES = max(4410, int(DEFAULT_RAOP_LATENCY_MS * 44.1))
    _LOGGER.info(
        "Latence RAOP définie à %d ms (%d frames)",
        DEFAULT_RAOP_LATENCY_MS,
        DEFAULT_RAOP_LATENCY_FRAMES,
    )


# Patch transparent de pyatv pour accepter un AudioSource personnalisé dans stream_file
_orig_open_source = audio_source.open_source


async def _patched_open_source(source: Any, *args: Any, **kwargs: Any) -> AudioSource:
    if isinstance(source, AudioSource):
        return source
    return await _orig_open_source(source, *args, **kwargs)


audio_source.open_source = _patched_open_source
raop.open_source = _patched_open_source


# Mesure de la régularité d'envoi des paquets RTP (diagnostic des hachures audio)
PACING_STATS = PacingStats()

_orig_send_packet = StreamClient._send_packet


async def _timed_send_packet(self: StreamClient, *args: Any, **kwargs: Any) -> int:
    num_sent = await _orig_send_packet(self, *args, **kwargs)
    if num_sent:
        PACING_STATS.record(time.perf_counter())
    return num_sent


StreamClient._send_packet = _timed_send_packet  # type: ignore[method-assign]

# Pertes réseau : le récepteur demande la retransmission des paquets RTP manquants
LOSS_STATS = LossStats()

_orig_retransmit = ControlClient._retransmit_lost_packets


def _counted_retransmit(self: ControlClient, request: Any, addr: Any) -> None:
    LOSS_STATS.record(int(request.lost_packets), time.perf_counter())
    _orig_retransmit(self, request, addr)


ControlClient._retransmit_lost_packets = _counted_retransmit  # type: ignore[method-assign]


class PipelineAudioSource(AudioSource):
    """Source audio AirPlay connectée au ring buffer d'un AudioPipeline."""

    def __init__(self, pipeline: AudioPipeline, sample_rate: int = 44100) -> None:
        self.pipeline = pipeline
        self._sample_rate = sample_rate
        self._channels = 2
        self._sample_size = 2  # 16-bit
        self._is_stopped = False
        self._first_read = True

    async def readframes(self, nframes: int) -> bytes:
        """Lit nframes audio (généralement 352 frames = 1408 octets à 44.1 kHz)."""
        if self._is_stopped:
            return b""

        if self._first_read:
            # Purge immédiate de tout résidu de buffer accumulé pendant la négociation RTSP
            self.pipeline.clear()
            self._first_read = False

        nbytes = nframes * self._channels * self._sample_size
        # pull() renvoie toujours nbytes (produit du silence si le système est muet)
        raw_pcm = self.pipeline.pull(nbytes)
        # Conversion obligatoire en Big-Endian (s16be) pour le protocole réseau RAOP / AirPlay
        return _to_audio_samples(raw_pcm)

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
        PACING_STATS.reset()
        LOSS_STATS.reset()
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
        """Règle le niveau de volume (0.0 à 100.0 ou 0.0 à 1.0)."""
        if self._atv:
            # Si le niveau est entre 0.0 et 1.0 (ex: 0.35), convertir en pourcentage (ex: 35.0%)
            target_pct = level * 100.0 if (0.0 <= level <= 1.0 and level > 0.0) else level
            clamped = max(0.0, min(100.0, target_pct))
            _LOGGER.info("Réglage du volume AirPlay : %.1f %%", clamped)
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
