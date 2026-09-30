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


def list_wasapi_loopback_devices() -> list[dict[str, Any]]:
    """Retourne la liste de tous les périphériques loopback WASAPI disponibles sur le système."""
    devices: list[dict[str, Any]] = []
    pa = pyaudio.PyAudio()
    try:
        try:
            wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            return devices

        default_loopback = None
        try:
            default_loopback = pa.get_default_wasapi_loopback()
        except Exception:  # noqa: BLE001
            pass

        default_idx = int(default_loopback["index"]) if default_loopback else -1

        for i in range(wasapi_info.get("deviceCount", 0)):
            try:
                info = pa.get_device_info_by_host_api_device_index(wasapi_info["index"], i)
                if info.get("isLoopbackDevice"):
                    name = str(info.get("name", ""))
                    name_lower = name.lower()
                    is_virtual = any(
                        keyword in name_lower
                        for keyword in ("cable", "vb-audio", "virtual", "voicemeeter")
                    )
                    devices.append({
                        "index": int(info["index"]),
                        "name": name,
                        "samplerate": int(info.get("defaultSampleRate", 48000)),
                        "channels": int(info.get("maxInputChannels", 2)),
                        "is_default": (int(info["index"]) == default_idx),
                        "is_virtual": is_virtual,
                    })
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Erreur lecture device WASAPI %d : %s", i, exc)
    finally:
        pa.terminate()
    return devices


def find_loopback_device(
    device_name_or_index: str | int | None = None,
    prefer_virtual: bool = True,
) -> dict[str, Any] | None:
    """Trouve le meilleur périphérique de capture loopback selon les préférences."""
    devices = list_wasapi_loopback_devices()
    if not devices:
        return None

    if isinstance(device_name_or_index, int):
        for dev in devices:
            if dev["index"] == device_name_or_index:
                return dev

    if isinstance(device_name_or_index, str) and device_name_or_index not in (
        "auto",
        "default-loopback",
        "",
    ):
        for dev in devices:
            if device_name_or_index.lower() in dev["name"].lower():
                return dev

    # 1. Privilégier le câble virtuel si demandé (permet le son dédié à l'enceinte avec PC muet)
    if prefer_virtual:
        for dev in devices:
            if dev["is_virtual"]:
                return dev

    # 2. Utiliser la sortie système par défaut (mode miroir)
    for dev in devices:
        if dev["is_default"]:
            return dev

    # 3. Premier périphérique loopback trouvé en secours
    return devices[0]


class LoopbackCapture:
    """Capture audio système via WASAPI Loopback."""

    def __init__(
        self,
        device_index: int | None = None,
        device_name: str | None = None,
        prefer_virtual: bool = True,
        max_buffered_blocks: int = 200,
    ) -> None:
        """Initialise la capture.

        Si device_index et device_name sont None, sélectionne automatiquement :
        - Le câble audio virtuel dédié si présent et prefer_virtual=True
        - Le périphérique loopback associé à la sortie par défaut sinon.
        """
        self._pa: pyaudio.PyAudio | None = None
        self._stream: pyaudio.Stream | None = None
        self._device_index = device_index
        self._requested_device_name = device_name
        self._prefer_virtual = prefer_virtual
        self._max_buffered_blocks = max_buffered_blocks
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=max_buffered_blocks)

        self._samplerate: int = 48000
        self._channels: int = 2
        self._dtype: str = "int16"
        self._device_name: str = ""
        self._is_virtual: bool = False
        self._overflow_count: int = 0
        self._is_running: bool = False

        self._init_device_info()

    def _init_device_info(self) -> None:
        """Détecte les caractéristiques du périphérique loopback sélectionné."""
        target = find_loopback_device(
            device_name_or_index=self._device_index
            if self._device_index is not None
            else self._requested_device_name,
            prefer_virtual=self._prefer_virtual,
        )
        if not target:
            raise AudioDeviceNotFoundError(
                "Aucun périphérique loopback WASAPI n'a été détecté sur votre système."
            )

        self._device_index = int(target["index"])
        self._device_name = str(target["name"])
        self._samplerate = int(target["samplerate"])
        self._channels = int(target["channels"])
        self._is_virtual = bool(target.get("is_virtual", False))
        _LOGGER.info(
            "Périphérique de capture sélectionné : '%s' (Index %d, Virtuel=%s)",
            self._device_name,
            self._device_index,
            self._is_virtual,
        )

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
    def is_virtual(self) -> bool:
        """Indique si la capture utilise un périphérique audio virtuel dédié."""
        return self._is_virtual

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
