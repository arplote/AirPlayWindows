"""Superviseur de session AirPlay : machine d'états, résilience et auto-réparation.

Phase 5 du projet AirPlayWindows.
Gère les transitions d'états, la reconnexion automatique avec backoff exponentiel et jitter,
et l'adaptation dynamique au changement de sortie audio Windows.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
from enum import Enum
from typing import Any, Callable

import pyatv
from pyatv.const import Protocol
from pyatv.interface import BaseConfig

from airplay_sender.audio_pipeline import AudioPipeline
from airplay_sender.capture import LoopbackCapture
from airplay_sender.devices_watch import AudioDeviceWatcher
from airplay_sender.metrics import MetricsLogger, SessionMetrics
from airplay_sender.streamer import AirPlayStreamer

_LOGGER = logging.getLogger("airplay_sender.supervisor")


class State(str, Enum):
    """États de la machine d'états du superviseur."""

    IDLE = "IDLE"
    DISCOVERING = "DISCOVERING"
    CONNECTING = "CONNECTING"
    STREAMING = "STREAMING"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class Supervisor:
    """Machine d'états de supervision résiliente pour la diffusion AirPlay."""

    def __init__(
        self,
        target_name: str,
        buffer_ms: int = 800,
        volume: float | None = None,
        credentials: str | None = None,
        on_state_change: Callable[[State], None] | None = None,
        sleep_func: Callable[[float], Any] | None = None,
    ) -> None:
        self.target_name = target_name
        self.buffer_ms = buffer_ms
        self.volume = volume
        self.credentials = credentials
        self.on_state_change = on_state_change
        self._sleep = sleep_func or asyncio.sleep

        self._state: State = State.IDLE
        self._stop_requested = False
        self._switch_device_requested: str | None = None

        # Composants internes
        self._capture: LoopbackCapture | None = None
        self._pipeline: AudioPipeline | None = None
        self._streamer: AirPlayStreamer = AirPlayStreamer()
        self._feeder_thread: threading.Thread | None = None
        self._feeder_stop_event = threading.Event()
        self._device_watcher: AudioDeviceWatcher | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._metrics_logger: MetricsLogger | None = None
        self.metrics = SessionMetrics()

        # Paramètres de backoff
        self._backoff_base_sec = 1.0
        self._backoff_max_sec = 30.0
        self._consecutive_failures = 0

    @property
    def state(self) -> State:
        return self._state

    def _set_state(self, new_state: State) -> None:
        if self._state == new_state:
            return
        old_state = self._state
        self._state = new_state
        _LOGGER.info("Transition d'état : %s -> %s", old_state.value, new_state.value)
        if self.on_state_change:
            try:
                self.on_state_change(new_state)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error("Erreur dans le callback on_state_change : %s", exc)

    def request_start(self) -> None:
        """Demande le démarrage du streaming."""
        self._stop_requested = False

    def request_stop(self) -> None:
        """Demande l'arrêt du streaming."""
        self._stop_requested = True
        self._set_state(State.STOPPED)
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()

    def request_switch_device(self, new_name: str) -> None:
        """Bascule la cible de diffusion vers une autre enceinte."""
        _LOGGER.info("Demande de bascule vers l'enceinte '%s'", new_name)
        self.target_name = new_name
        self._switch_device_requested = new_name
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()

    def _on_default_audio_device_changed(self, new_device_info: dict[str, Any]) -> None:
        """Gère le branchement/débranchement d'un casque ou changement de sortie Windows."""
        _LOGGER.info(
            "Sortie audio système changée : '%s'. Réinitialisation de la capture...",
            new_device_info.get("name"),
        )
        self._restart_capture()

    def _restart_capture(self) -> None:
        """Redémarre la capture audio sans interrompre la session AirPlay."""
        self._feeder_stop_event.set()
        if self._feeder_thread and self._feeder_thread.is_alive():
            self._feeder_thread.join(timeout=1.0)

        if self._capture:
            self._capture.stop()

        try:
            self._capture = LoopbackCapture()
            self._capture.start()

            # Initialiser ou mettre à jour le pipeline si la fréquence d'échantillonnage a changé
            if self._pipeline is None or self._pipeline.in_rate != self._capture.samplerate:
                _LOGGER.info(
                    "Configuration du pipeline : in_rate %d Hz -> out_rate 44100 Hz",
                    self._capture.samplerate,
                )
                self._pipeline = AudioPipeline(
                    in_rate=self._capture.samplerate,
                    in_channels=self._capture.channels,
                    out_rate=44100,
                    out_channels=2,
                    buffer_ms=self.buffer_ms,
                )

            self._feeder_stop_event.clear()
            self._feeder_thread = threading.Thread(
                target=self._capture_feeder_worker,
                daemon=True,
            )
            self._feeder_thread.start()
            _LOGGER.info("Capture audio redémarrée avec succès.")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Échec du redémarrage de la capture : %s", exc)

    def _capture_feeder_worker(self) -> None:
        """Achemine les blocs capturés vers le pipeline."""
        while not self._feeder_stop_event.is_set():
            if not self._capture:
                break
            data = self._capture.read(timeout=0.02)
            if data and self._pipeline:
                self._pipeline.push(data)

    async def _calculate_backoff_and_wait(self) -> None:
        """Calcule un délai de backoff exponentiel avec gigue (jitter) et attend."""
        delay = min(
            self._backoff_max_sec,
            self._backoff_base_sec * (2**self._consecutive_failures),
        )
        jitter = delay * random.uniform(-0.1, 0.1)
        actual_delay = max(0.5, delay + jitter)

        _LOGGER.info(
            "Attente de reconnexion : %.1fs (échec consécutif %d)...",
            actual_delay,
            self._consecutive_failures,
        )
        await self._sleep(actual_delay)

    async def run(self) -> None:
        """Boucle principale de supervision."""
        loop = asyncio.get_running_loop()

        self._device_watcher = AudioDeviceWatcher(
            on_device_changed=self._on_default_audio_device_changed,
            poll_interval=2.0,
        )
        self._device_watcher.start()

        def get_current_metrics() -> SessionMetrics:
            if self._pipeline:
                self.metrics.total_underruns = self._pipeline.stats.underruns
                self.metrics.total_overflows = self._pipeline.stats.overflows
                self.metrics.total_silence_bytes = self._pipeline.stats.silence_bytes_generated
                self.metrics.record_buffer_level(self._pipeline.level_ms)
            return self.metrics

        self._metrics_logger = MetricsLogger(get_current_metrics, interval_seconds=30.0)
        self._metrics_logger.start()

        try:
            while not self._stop_requested:
                if self._switch_device_requested:
                    self._switch_device_requested = None
                    self._consecutive_failures = 0
                    await self._streamer.close()

                # 1. ÉTAPE : DÉCOUVERTE (DISCOVERING)
                self._set_state(State.DISCOVERING)
                target_device: BaseConfig | None = None
                try:
                    devices = await pyatv.scan(loop, timeout=5)
                    target_device = next(
                        (d for d in devices if self.target_name.lower() in d.name.lower()),
                        None,
                    )
                except Exception as scan_exc:  # noqa: BLE001
                    _LOGGER.warning("Erreur lors de la découverte : %s", scan_exc)

                if not target_device:
                    _LOGGER.warning(
                        "Enceinte '%s' introuvable sur le réseau. Nouvelle tentative...",
                        self.target_name,
                    )
                    self._consecutive_failures += 1
                    self.metrics.reconnect_count += 1
                    self._set_state(State.RECONNECTING)
                    await self._calculate_backoff_and_wait()
                    continue

                # 2. ÉTAPE : CONNEXION (CONNECTING)
                self._set_state(State.CONNECTING)
                try:
                    await self._streamer.connect(
                        target_device,
                        credentials=self.credentials,
                        protocol=Protocol.RAOP,
                    )
                    if self.volume is not None:
                        await self._streamer.set_volume(self.volume)
                    self._consecutive_failures = 0
                except Exception as conn_exc:  # noqa: BLE001
                    _LOGGER.error("Échec de connexion à '%s' : %s", target_device.name, conn_exc)
                    self._consecutive_failures += 1
                    self.metrics.reconnect_count += 1
                    self._set_state(State.RECONNECTING)
                    await self._calculate_backoff_and_wait()
                    continue

                # 3. ÉTAPE : PRÉPARATION DE LA CAPTURE & PIPELINE
                self._restart_capture()

                # 4. ÉTAPE : DIFFUSION EN DIRECT (STREAMING)
                self._set_state(State.STREAMING)
                try:
                    if self._pipeline:
                        self._stream_task = asyncio.create_task(
                            self._streamer.stream(self._pipeline)
                        )
                        await self._stream_task
                except (asyncio.CancelledError, KeyboardInterrupt):
                    if self._stop_requested:
                        break
                except Exception as stream_exc:  # noqa: BLE001
                    _LOGGER.warning("Interruption du streaming : %s", stream_exc)
                    self._consecutive_failures += 1
                    self.metrics.reconnect_count += 1
                    self._set_state(State.RECONNECTING)
                    await self._calculate_backoff_and_wait()
                finally:
                    self._stream_task = None

        finally:
            self._set_state(State.STOPPED)
            if self._device_watcher:
                self._device_watcher.stop()
            if self._metrics_logger:
                self._metrics_logger.stop()
            self._feeder_stop_event.set()
            if self._feeder_thread and self._feeder_thread.is_alive():
                self._feeder_thread.join(timeout=1.0)
            if self._capture:
                self._capture.stop()
            await self._streamer.close()
            _LOGGER.info("Superviseur arrêté.")
