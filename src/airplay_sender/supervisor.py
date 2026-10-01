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
from airplay_sender.metrics import LoopLagMonitor, MetricsLogger, SessionMetrics
from airplay_sender.mmcss import disable_mmcss, enable_mmcss
from airplay_sender.netprobe import NetworkProbe
from airplay_sender.power import PowerNotifier
from airplay_sender.streamer import (
    LOSS_STATS,
    PACING_STATS,
    AirPlayStreamer,
    set_raop_latency_ms,
)
from airplay_sender.windows_volume import WindowsVolumeWatcher

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

    # Délai après la reprise de Windows avant de se reconnecter (retour du Wi-Fi/DHCP)
    RESUME_SETTLE_SEC = 3.0

    def __init__(
        self,
        target_name: str,
        buffer_ms: int = 80,
        raop_latency_ms: int = 250,
        volume: float | None = None,
        credentials: str | None = None,
        on_state_change: Callable[[State], None] | None = None,
        sleep_func: Callable[[float], Any] | None = None,
        prefer_virtual_cable: bool = True,
        capture_device: str | None = None,
        adaptive_latency: bool = True,
    ) -> None:
        self.target_name = target_name
        self.buffer_ms = buffer_ms
        self.raop_latency_ms = raop_latency_ms
        self.volume = volume
        self.credentials = credentials
        self.on_state_change = on_state_change
        self._sleep = sleep_func or asyncio.sleep
        self.prefer_virtual_cable = prefer_virtual_cable
        self.capture_device = capture_device
        self.adaptive_latency = adaptive_latency
        self._adaptive_task: asyncio.Task[None] | None = None

        set_raop_latency_ms(self.raop_latency_ms)

        self._state: State = State.IDLE
        self._stop_requested = False
        self._switch_device_requested: str | None = None
        self._suspended = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._power_notifier: PowerNotifier | None = None
        self._network_probe = NetworkProbe()

        # Composants internes
        self._capture: LoopbackCapture | None = None
        self._pipeline: AudioPipeline | None = None
        self._streamer: AirPlayStreamer = AirPlayStreamer()
        self._feeder_thread: threading.Thread | None = None
        self._feeder_stop_event = threading.Event()
        self._device_watcher: AudioDeviceWatcher | None = None
        self._volume_watcher: WindowsVolumeWatcher | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._metrics_logger: MetricsLogger | None = None
        self._loop_lag_monitor: LoopLagMonitor | None = None
        self.metrics = SessionMetrics()

        # Paramètres de backoff
        self._backoff_base_sec = 1.0
        self._backoff_max_sec = 30.0
        self._consecutive_failures = 0

    @property
    def state(self) -> State:
        return self._state

    @property
    def pipeline(self) -> AudioPipeline | None:
        """Accès au pipeline audio actif pour les métriques temps réel."""
        return self._pipeline

    @property
    def capture_device_name(self) -> str:
        """Nom du périphérique actuellement capturé."""
        return self._capture.device_name if self._capture else "Non initialisé"

    @property
    def is_capture_virtual(self) -> bool:
        """Indique si la capture s'effectue sur un câble audio virtuel dédié."""
        return self._capture.is_virtual if self._capture else False

    def get_audio_level(self) -> float:
        """Retourne le niveau de crête audio courant (0.0 à 1.0) ou 0.0 si inactif."""
        if self._pipeline:
            return self._pipeline.stats.current_peak
        return 0.0

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

    def set_latency(self, buffer_ms: int, raop_latency_ms: int) -> None:
        """Modifie les paramètres de latence et les applique au pipeline et au streamer."""
        self.buffer_ms = buffer_ms
        self.raop_latency_ms = raop_latency_ms
        set_raop_latency_ms(raop_latency_ms)
        if self._pipeline:
            self._pipeline.set_buffer_ms(buffer_ms)
        self.metrics.estimated_latency_ms = float(buffer_ms + raop_latency_ms)

    async def set_volume(self, level: float) -> None:
        """Modifie le niveau de volume (0.0 à 1.0) sur l'enceinte active et le pipeline."""
        self.volume = level
        if self._pipeline:
            self._pipeline.set_volume_scale(level)

        # Synchroniser également le curseur Windows si pycaw est disponible
        try:
            import comtypes
            from pycaw.pycaw import AudioUtilities

            comtypes.CoInitialize()
            spk = AudioUtilities.GetSpeakers()
            if spk and spk.EndpointVolume:
                spk.EndpointVolume.SetMasterVolumeLevelScalar(level, None)
            comtypes.CoUninitialize()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Synchronisation volume Windows non supportée : %s", exc)

    def _on_default_audio_device_changed(self, new_device_info: dict[str, Any]) -> None:
        """Gère le branchement/débranchement d'un casque ou changement de sortie Windows."""
        # Si un câble virtuel dédié est capturé, le changement de sortie par défaut dans
        # Windows permet d'activer ou désactiver l'envoi vers l'enceinte sans couper la session.
        if self._capture and self._capture.is_virtual:
            _LOGGER.info(
                "Sortie Windows modifiée (%s). Capture maintenue sur le câble virtuel dédié '%s'.",
                new_device_info.get("name"),
                self._capture.device_name,
            )
            return

        _LOGGER.info(
            "Sortie audio système changée : '%s'. Réinitialisation de la capture...",
            new_device_info.get("name"),
        )
        self._restart_capture()

    def _on_power_suspend(self) -> None:
        """Appelé depuis un thread système à l'entrée en veille de Windows."""
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._enter_suspend)

    def _on_power_resume(self) -> None:
        """Appelé depuis un thread système à la reprise de Windows."""
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._exit_suspend)

    def _enter_suspend(self) -> None:
        if self._suspended:
            return
        _LOGGER.info("Mise en veille de Windows : arrêt du streaming et de la capture.")
        self._suspended = True
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()

    def _exit_suspend(self) -> None:
        if not self._suspended:
            return
        _LOGGER.info("Reprise de Windows : reconnexion dans %.0f s.", self.RESUME_SETTLE_SEC)
        self._suspended = False

    async def _pause_for_suspend(self) -> None:
        """Libère connexion et capture pendant la veille, puis laisse le réseau revenir."""
        self._set_state(State.RECONNECTING)
        await self._streamer.close()
        self._stop_capture()
        while self._suspended and not self._stop_requested:
            await asyncio.sleep(0.5)
        if not self._stop_requested:
            await asyncio.sleep(self.RESUME_SETTLE_SEC)

    def _stop_capture(self) -> None:
        """Arrête le thread d'alimentation et ferme le flux de capture."""
        self._feeder_stop_event.set()
        if self._feeder_thread and self._feeder_thread.is_alive():
            self._feeder_thread.join(timeout=1.0)
        if self._capture:
            self._capture.stop()
            self._capture = None

    def _restart_capture(self) -> None:
        """Redémarre la capture audio sans interrompre la session AirPlay."""
        self._feeder_stop_event.set()
        if self._feeder_thread and self._feeder_thread.is_alive():
            self._feeder_thread.join(timeout=1.0)

        if self._capture:
            self._capture.stop()

        try:
            self._capture = LoopbackCapture(
                device_name=self.capture_device,
                prefer_virtual=self.prefer_virtual_cable,
            )
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

            if self.volume is not None and self._pipeline:
                self._pipeline.set_volume_scale(self.volume)

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
        mmcss_handle = enable_mmcss("Pro Audio")
        try:
            while not self._feeder_stop_event.is_set():
                if not self._capture:
                    break
                data = self._capture.read(timeout=0.02)
                if data and self._pipeline:
                    self._pipeline.push(data)
        finally:
            disable_mmcss(mmcss_handle)

    async def _adaptive_latency_loop(self) -> None:
        """Ajuste dynamiquement le tampon logiciel selon la gigue observée (AIMD).

        S'appuie sur les compteurs déjà suivis par le pipeline (underruns/overflows) :
        en cas d'incident sur une fenêtre de quelques secondes, le tampon augmente par
        paliers (jusqu'à un plafond) pour absorber la gigue réseau ; en l'absence
        d'incident, il redescend doucement vers la valeur de base choisie par
        l'utilisateur (profil de latence). Ne touche jamais à la latence RAOP
        matérielle négociée, seulement au tampon logiciel côté app.
        """
        step_up_ms = 30
        step_down_ms = 15
        window_sec = 5.0
        max_headroom_ms = 250

        current_ms = self.buffer_ms
        last_underruns = 0
        last_overflows = 0

        while not self._stop_requested:
            # Cadence réelle indépendante du sleep_func injectable (réservé aux tests
            # de backoff de reconnexion) : cette boucle doit toujours véritablement
            # suspendre le task pour ne jamais monopoliser la boucle asyncio.
            await asyncio.sleep(window_sec)

            if not self.adaptive_latency or not self._pipeline or self._state != State.STREAMING:
                continue

            underruns = self._pipeline.stats.underruns
            overflows = self._pipeline.stats.overflows
            incidents = (underruns - last_underruns) + (overflows - last_overflows)
            last_underruns, last_overflows = underruns, overflows

            baseline_ms = self.buffer_ms
            ceiling_ms = min(400, baseline_ms + max_headroom_ms)
            current_ms = max(current_ms, baseline_ms)

            if incidents > 0:
                current_ms = min(ceiling_ms, current_ms + step_up_ms)
            else:
                current_ms = max(baseline_ms, current_ms - step_down_ms)

            if current_ms != self._pipeline.buffer_ms:
                self._pipeline.set_buffer_ms(current_ms)
                _LOGGER.debug(
                    "Latence adaptative : tampon ajusté à %d ms (incidents=%d, base=%d ms)",
                    current_ms,
                    incidents,
                    baseline_ms,
                )

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

        # Surveillance du volume Windows en temps réel (touchpad, clavier, mixeur)
        def _on_win_volume(scalar: float, is_muted: bool) -> None:
            eff_vol = 0.0 if is_muted else scalar
            self.volume = eff_vol
            if self._pipeline:
                self._pipeline.set_volume_scale(eff_vol)

        self._volume_watcher = WindowsVolumeWatcher(on_volume_changed=_on_win_volume)
        self._volume_watcher.start()

        def get_current_metrics() -> SessionMetrics:
            if self._pipeline:
                self.metrics.total_underruns = self._pipeline.stats.underruns
                self.metrics.total_overflows = self._pipeline.stats.overflows
                self.metrics.total_silence_bytes = self._pipeline.stats.silence_bytes_generated
                self.metrics.record_buffer_level(self._pipeline.level_ms)
            self.metrics.pacing_summary = PACING_STATS.snapshot_and_reset()
            self.metrics.loss_summary = LOSS_STATS.snapshot_and_reset()
            self.metrics.network_summary = self._network_probe.snapshot_and_reset()
            if self._loop_lag_monitor:
                self.metrics.loop_lag_summary = self._loop_lag_monitor.snapshot_and_reset()
            return self.metrics

        def stall_context() -> str:
            level = f"buffer={self._pipeline.level_ms:.0f}ms, " if self._pipeline else ""
            return f"{level}underruns={self.metrics.total_underruns}"

        PACING_STATS.context_func = stall_context
        self._loop_lag_monitor = LoopLagMonitor(stall_context)
        self._loop_lag_monitor.start()

        self._metrics_logger = MetricsLogger(get_current_metrics, interval_seconds=30.0)
        self._metrics_logger.start()

        self._adaptive_task = asyncio.create_task(self._adaptive_latency_loop())

        self._loop = loop
        self._power_notifier = PowerNotifier(self._on_power_suspend, self._on_power_resume)
        self._power_notifier.start()

        try:
            while not self._stop_requested:
                if self._suspended:
                    await self._pause_for_suspend()
                    continue

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
                    # Régler l'enceinte au niveau nominal pour que l'atténuation logicielle
                    # pilote 100% de la dynamique sonore de façon ultra-réactive
                    try:
                        await self._streamer.set_volume(1.0)
                    except Exception as vol_exc:  # noqa: BLE001
                        _LOGGER.debug("Initialisation du volume matériel : %s", vol_exc)
                    self._consecutive_failures = 0
                    self._network_probe.start(str(target_device.address))
                except Exception as conn_exc:  # noqa: BLE001
                    _LOGGER.error("Échec de connexion à '%s' : %s", target_device.name, conn_exc)
                    self._consecutive_failures += 1
                    self.metrics.reconnect_count += 1
                    self._set_state(State.RECONNECTING)
                    await self._calculate_backoff_and_wait()
                    continue

                if self._suspended:
                    continue  # veille entamée pendant la connexion : _pause_for_suspend la libère

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
            if self._adaptive_task and not self._adaptive_task.done():
                self._adaptive_task.cancel()
            if self._volume_watcher:
                self._volume_watcher.stop()
                self._volume_watcher = None
            if self._device_watcher:
                self._device_watcher.stop()
            if self._metrics_logger:
                self._metrics_logger.stop()
            if self._loop_lag_monitor:
                self._loop_lag_monitor.stop()
                self._loop_lag_monitor = None
            if self._power_notifier:
                self._power_notifier.stop()
                self._power_notifier = None
            self._network_probe.stop()
            report = self._network_probe.session_report()
            if report:
                _LOGGER.info(report)
            self._feeder_stop_event.set()
            if self._feeder_thread and self._feeder_thread.is_alive():
                self._feeder_thread.join(timeout=1.0)
            if self._capture:
                self._capture.stop()
            await self._streamer.close()
            _LOGGER.info("Superviseur arrêté.")
