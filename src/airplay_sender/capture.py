"""Module de capture audio Windows via WASAPI Loopback (PyAudioWPatch).

Phase 3 du projet AirPlayWindows.
Permet d'intercepter tout le son joué par le système Windows sur la sortie par défaut.
"""

from __future__ import annotations

import logging
import queue
from typing import Any, Self

import pyaudiowpatch as pyaudio

_LOGGER = logging.getLogger("airplay_sender.capture")


class AudioDeviceNotFoundError(Exception):
    """Exception levée si aucun périphérique loopback WASAPI n'est trouvé."""


class LoopbackCapture:
    """Capture audio système via WASAPI Loopback."""

    def __init__(
        self,
        device_index: int | None = None,
        max_buffered_blocks: int = 200,
    ) -> None:
        """Initialise la capture.

        Si device_index est None, utilise le loopback associé à la sortie par défaut.
        """
        self._pa: pyaudio.PyAudio | None = None
        self._stream: pyaudio.Stream | None = None
        self._device_index = device_index
        self._max_buffered_blocks = max_buffered_blocks
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=max_buffered_blocks)

        self._samplerate: int = 48000
        self._channels: int = 2
        self._dtype: str = "int16"
        self._device_name: str = ""
        self._overflow_count: int = 0
        self._is_running: bool = False

        self._init_device_info()

    def _init_device_info(self) -> None:
        """Détecte les caractéristiques du périphérique loopback sélectionné."""
        pa = pyaudio.PyAudio()
        try:
            if self._device_index is None:
                dev = pa.get_default_wasapi_loopback()
                if not dev:
                    raise AudioDeviceNotFoundError(
                        "Aucun périphérique loopback WASAPI par défaut n'a été détecté."
                    )
            else:
                dev = pa.get_device_info_by_index(self._device_index)
                if not dev.get("isLoopbackDevice"):
                    _LOGGER.warning(
                        "Le périphérique d'index %d n'est pas marqué comme loopback.",
                        self._device_index,
                    )

            self._device_index = int(dev["index"])
            self._device_name = str(dev.get("name", "Inconnu"))
            self._samplerate = int(dev.get("defaultSampleRate", 48000))
            self._channels = int(dev.get("maxInputChannels", 2))
        finally:
            pa.terminate()

    @property
    def samplerate(self) -> int:
        """Fréquence d'échantillonnage native en Hz."""
        return self._samplerate

    @property
    def channels(self) -> int:
        """Nombre de canaux natifs."""
        return self._channels

    @property
    def dtype(self) -> str:
        """Type de données capturé ('int16')."""
        return self._dtype

    @property
    def device_name(self) -> str:
        """Nom du périphérique audio capturé."""
        return self._device_name

    @property
    def is_running(self) -> bool:
        """Indique si la capture est active."""
        return self._is_running

    @property
    def overflow_count(self) -> int:
        """Nombre de blocs abandonnés par saturation de la file."""
        return self._overflow_count

    def start(self) -> None:
        """Démarre le flux de capture WASAPI loopback."""
        if self._is_running:
            return

        self._pa = pyaudio.PyAudio()
        # Vider la queue au redémarrage
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        def _audio_callback(
            in_data: bytes | None,
            frame_count: int,
            time_info: dict[str, Any],
            status_flags: int,
        ) -> tuple[None, int]:
            # Callback temps réel : opération ultra-rapide, aucune allocation lourde
            if in_data:
                try:
                    self._queue.put_nowait(in_data)
                except queue.Full:
                    self._overflow_count += 1
            return (None, pyaudio.paContinue)

        _LOGGER.info(
            "Ouverture du flux WASAPI Loopback sur '%s' (%d Hz, %d canaux, int16)...",
            self._device_name,
            self._samplerate,
            self._channels,
        )

        try:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=self._channels,
                rate=self._samplerate,
                input=True,
                input_device_index=self._device_index,
                stream_callback=_audio_callback,
            )
            self._stream.start_stream()
            self._is_running = True
        except Exception as exc:
            self.stop()
            raise RuntimeError(f"Échec de l'ouverture du loopback WASAPI : {exc}") from exc

    def stop(self) -> None:
        """Arrête la capture et libère les ressources audio."""
        self._is_running = False
        if self._stream:
            try:
                if self._stream.is_active():
                    self._stream.stop_stream()
                self._stream.close()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Erreur lors de la fermeture du stream : %s", exc)
            self._stream = None

        if self._pa:
            try:
                self._pa.terminate()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Erreur lors de la fermeture de PyAudio : %s", exc)
            self._pa = None

    def read(self, timeout: float = 0.1) -> bytes | None:
        """Lit un bloc de données audio PCM brutes depuis la file.

        Retourne None si le timeout est atteint (silence ou pas de son joué).
        """
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
