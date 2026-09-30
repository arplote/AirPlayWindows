"""Surveillance et synchronisation en direct du volume maître de Windows.

Permet de répercuter instantanément les gestes de touchpad (ex: glissement à 4 doigts),
les touches de volume clavier (Fn+Volume) et le curseur Windows sur le flux audio
envoyé à l'enceinte via atténuation logicielle ultra-réactive.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

_LOGGER = logging.getLogger("airplay_sender.volume")
logging.getLogger("comtypes").setLevel(logging.WARNING)


class WindowsVolumeWatcher:
    """Surveille le volume maître et le mute de la sortie audio active sous Windows."""

    def __init__(
        self,
        on_volume_changed: Callable[[float, bool], None],
        poll_interval: float = 0.05,
    ) -> None:
        """Initialise le surveillant.

        :param on_volume_changed: Callback appelé avec (scalar: float [0..1], is_muted: bool).
        :param poll_interval: Intervalle de scrutation en secondes (50 ms par défaut).
        """
        self.on_volume_changed = on_volume_changed
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_scalar: float | None = None
        self._last_mute: bool | None = None

    def start(self) -> None:
        """Démarre le thread de surveillance du volume."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="WindowsVolumeWatcher",
            daemon=True,
        )
        self._thread.start()
        _LOGGER.debug("WindowsVolumeWatcher démarré (polling %.2fs).", self.poll_interval)

    def stop(self) -> None:
        """Arrête proprement le thread de surveillance."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def _run_loop(self) -> None:
        """Boucle de scrutation COM temps réel."""
        try:
            import comtypes
            from pycaw.pycaw import AudioUtilities
        except ImportError as exc:
            _LOGGER.warning("pycaw/comtypes non disponible : %s", exc)
            return

        comtypes.CoInitialize()
        endpoint = None
        tick_count = 0

        try:
            while not self._stop_event.is_set():
                tick_count += 1
                try:
                    # Rafraîchir l'endpoint toutes les secondes (20 ticks) au cas où la sortie
                    # audio par défaut change sous Windows
                    if endpoint is None or (tick_count % 20 == 0):
                        spk = AudioUtilities.GetSpeakers()
                        if spk:
                            endpoint = spk.EndpointVolume

                    if endpoint is not None:
                        scalar = float(endpoint.GetMasterVolumeLevelScalar())
                        mute = bool(endpoint.GetMute())

                        if (
                            self._last_scalar is None
                            or abs(scalar - self._last_scalar) > 0.005
                            or mute != self._last_mute
                        ):
                            self._last_scalar = scalar
                            self._last_mute = mute
                            try:
                                self.on_volume_changed(scalar, mute)
                            except Exception as exc:  # noqa: BLE001
                                _LOGGER.debug("Erreur dans on_volume_changed : %s", exc)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("Erreur lecture volume Windows, réessai : %s", exc)
                    endpoint = None

                time.sleep(self.poll_interval)
        finally:
            comtypes.CoUninitialize()
