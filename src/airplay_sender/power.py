"""Notifications de mise en veille / reprise de Windows, sans fenêtre cachée.

Utilise RegisterSuspendResumeNotification (user32.dll) en mode callback : Windows
appelle la fonction sur un thread système à l'entrée en veille et à la reprise. Cela
permet d'arrêter proprement le streaming et la capture avant que le réseau et les
périphériques audio ne soient coupés, au lieu de tenter une reconnexion en pleine
transition (source d'erreurs réseau et de crash natif observés).
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any, Callable

_LOGGER = logging.getLogger("airplay_sender.power")

_is_windows = sys.platform == "win32"

PBT_APMSUSPEND = 0x4
PBT_APMRESUMESUSPEND = 0x7
PBT_APMRESUMEAUTOMATIC = 0x12
DEVICE_NOTIFY_CALLBACK = 2


class _SubscribeParameters(ctypes.Structure):
    _fields_ = [("Callback", ctypes.c_void_p), ("Context", ctypes.c_void_p)]


class PowerNotifier:
    """Appelle on_suspend / on_resume lors des transitions d'alimentation Windows.

    Les callbacks sont invoqués depuis un thread système : ils doivent être rapides et
    thread-safe (par exemple en déléguant à la boucle asyncio via call_soon_threadsafe).
    """

    def __init__(self, on_suspend: Callable[[], None], on_resume: Callable[[], None]) -> None:
        self._on_suspend = on_suspend
        self._on_resume = on_resume
        self._handle: Any = None
        # Références conservées : le GC ne doit pas libérer le callback ni sa structure
        self._callback: Any = None
        self._params: _SubscribeParameters | None = None

    def handle_event(self, event_type: int) -> None:
        """Route un évènement PBT_* vers le bon rappel (jamais d'exception)."""
        try:
            if event_type == PBT_APMSUSPEND:
                self._on_suspend()
            elif event_type in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                self._on_resume()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Erreur dans le rappel d'alimentation : %s", exc)

    def start(self) -> bool:
        """Active les notifications. Renvoie False si indisponible (non-Windows, erreur)."""
        if not _is_windows or self._handle:
            return bool(self._handle)

        try:
            callback_type = ctypes.WINFUNCTYPE(
                ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p
            )

            def _callback(context: Any, event_type: int, setting: Any) -> int:
                self.handle_event(int(event_type))
                return 0

            self._callback = callback_type(_callback)
            self._params = _SubscribeParameters(
                ctypes.cast(self._callback, ctypes.c_void_p), None
            )

            user32 = ctypes.WinDLL("user32.dll")
            user32.RegisterSuspendResumeNotification.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
            ]
            user32.RegisterSuspendResumeNotification.restype = ctypes.c_void_p
            self._handle = user32.RegisterSuspendResumeNotification(
                ctypes.addressof(self._params), DEVICE_NOTIFY_CALLBACK
            )
            if not self._handle:
                _LOGGER.debug("RegisterSuspendResumeNotification a échoué.")
                return False
            _LOGGER.debug("Notifications de veille/reprise activées.")
            return True
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Notifications d'alimentation indisponibles : %s", exc)
            self._handle = None
            return False

    def stop(self) -> None:
        if not _is_windows or not self._handle:
            return
        try:
            user32 = ctypes.WinDLL("user32.dll")
            user32.UnregisterSuspendResumeNotification.argtypes = [ctypes.c_void_p]
            user32.UnregisterSuspendResumeNotification(self._handle)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Désinscription des notifications d'alimentation : %s", exc)
        finally:
            self._handle = None
            self._callback = None
            self._params = None
