"""Garantie d'instance unique via Mutex nommé Windows.

Phase 7 du projet AirPlayWindows.
Empêche l'exécution simultanée de plusieurs instances de l'application.
"""

from __future__ import annotations

import ctypes
import logging
import sys

_LOGGER = logging.getLogger("airplay_sender.single_instance")

ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Gestionnaire de verrou inter-processus via Mutex Win32."""

    def __init__(self, mutex_name: str = "Local\\AirPlaySender_SingleInstance_Mutex") -> None:
        self.mutex_name = mutex_name
        self.mutex_handle: int | None = None
        self.already_running = False

    def acquire(self) -> bool:
        """Tente de créer ou verrouiller le Mutex.

        Returns:
            True si nous sommes la seule instance active, False si une autre instance tourne déjà.
        """
        if sys.platform != "win32":
            # Sur les systèmes hors Windows (ex: tests sous Linux/macOS), autoriser
            return True

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self.mutex_handle = kernel32.CreateMutexW(None, True, self.mutex_name)
        last_error = kernel32.GetLastError()

        if last_error == ERROR_ALREADY_EXISTS:
            _LOGGER.warning(
                "Une autre instance d'AirPlay Sender est déjà en cours d'exécution (Mutex: %s).",
                self.mutex_name,
            )
            self.already_running = True
            if self.mutex_handle:
                kernel32.CloseHandle(self.mutex_handle)
                self.mutex_handle = None
            return False

        self.already_running = False
        _LOGGER.debug("Mutex d'instance unique acquis (%s).", self.mutex_name)
        return True

    def release(self) -> None:
        """Libère le handle du Mutex."""
        if sys.platform == "win32" and self.mutex_handle:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.ReleaseMutex(self.mutex_handle)
            kernel32.CloseHandle(self.mutex_handle)
            self.mutex_handle = None
            _LOGGER.debug("Mutex libéré.")

    def __enter__(self) -> SingleInstance:
        if not self.acquire():
            raise RuntimeError("Une autre instance de l'application est déjà active.")
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    def __del__(self) -> None:
        self.release()
