"""Surveillance du changement de périphérique audio de sortie par défaut de Windows.

Phase 5 du projet AirPlayWindows.
Surveille (polling toutes les 2s) l'identité du périphérique WASAPI Loopback par défaut.
Déclenche un rappel si l'utilisateur branche un casque ou change de périphérique de sortie.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

import pyaudiowpatch as pyaudio

from airplay_sender.capture import PORTAUDIO_LOCK

_LOGGER = logging.getLogger("airplay_sender.devices_watch")


class AudioDeviceWatcher:
    """Surveille les modifications de la sortie audio système par défaut sous Windows."""

    def __init__(
        self,
        on_device_changed: Callable[[dict[str, Any]], None],
        poll_interval: float = 2.0,
    ) -> None:
        self.on_device_changed = on_device_changed
        self.poll_interval = poll_interval
        self._current_device_name: str | None = None
        self._current_device_index: int | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._check_initial_device()

    def _check_initial_device(self) -> None:
        with PORTAUDIO_LOCK:
            pa = pyaudio.PyAudio()
            try:
                dev = pa.get_default_wasapi_loopback()
                if dev:
                    self._current_device_name = str(dev.get("name"))
                    self._current_device_index = int(dev.get("index", -1))
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Impossible d'obtenir le loopback initial : %s", exc)
            finally:
                pa.terminate()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="AudioDeviceWatcher",
            daemon=True,
        )
        self._thread.start()
        _LOGGER.debug("AudioDeviceWatcher démarré (polling %.1fs).", self.poll_interval)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._stop_event.wait(self.poll_interval):
                break

            try:
                # Le verrou n'est tenu que pendant l'interrogation de PortAudio : le rappel
                # (qui redémarre la capture et reprend le verrou) s'exécute après.
                with PORTAUDIO_LOCK:
                    pa = pyaudio.PyAudio()
                    try:
                        dev = pa.get_default_wasapi_loopback()
                    finally:
                        pa.terminate()
                if not dev:
                    continue

                dev_name = str(dev.get("name"))
                dev_index = int(dev.get("index", -1))

                if self._current_device_name is not None and dev_name != self._current_device_name:
                    _LOGGER.info(
                        "Changement de sortie audio détecté : '%s' -> '%s'",
                        self._current_device_name,
                        dev_name,
                    )
                    self._current_device_name = dev_name
                    self._current_device_index = dev_index
                    try:
                        self.on_device_changed(dict(dev))
                    except Exception as cb_exc:  # noqa: BLE001
                        _LOGGER.error("Erreur dans le rappel on_device_changed : %s", cb_exc)
                else:
                    self._current_device_name = dev_name
                    self._current_device_index = dev_index
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Erreur lors du sondage de la sortie audio : %s", exc)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        _LOGGER.debug("AudioDeviceWatcher arrêté.")
