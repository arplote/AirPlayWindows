"""Intégration MMCSS (Multimedia Class Scheduler Service) pour les threads temps réel.

Phase 10 (diagnostic hachures audio) du projet AirPlayWindows.
MMCSS est le mécanisme officiel de Windows pour donner une priorité d'ordonnancement
élevée aux threads audio/vidéo sensibles à la latence, sans les risques d'un
SetThreadPriority brut : le service throttle automatiquement le thread s'il
consomme trop de CPU, ce qui évite de geler le reste du système.
Utilisé par WASAPI, les DAWs et tous les vrais logiciels audio temps réel.
"""

from __future__ import annotations

import ctypes
import logging
import sys

_LOGGER = logging.getLogger("airplay_sender.mmcss")

_is_windows = sys.platform == "win32"


def enable_mmcss(task_name: str = "Pro Audio") -> int | None:
    """Associe le thread appelant courant à la classe de tâche MMCSS donnée.

    Renvoie un handle à passer à `disable_mmcss()`, ou None si indisponible
    (plateforme non-Windows, ou service MMCSS absent/désactivé).
    """
    if not _is_windows:
        return None

    try:
        avrt = ctypes.WinDLL("avrt.dll")
        task_index = ctypes.c_ulong(0)
        handle = avrt.AvSetMmThreadCharacteristicsW(task_name, ctypes.byref(task_index))
        if not handle:
            _LOGGER.debug("AvSetMmThreadCharacteristicsW a échoué pour '%s'.", task_name)
            return None
        _LOGGER.debug("Thread associé à la classe MMCSS '%s'.", task_name)
        return int(handle)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("MMCSS indisponible (%s) : %s", task_name, exc)
        return None


def disable_mmcss(handle: int | None) -> None:
    """Détache le thread courant de MMCSS (à appeler avant la fin du thread)."""
    if not _is_windows or not handle:
        return

    try:
        avrt = ctypes.WinDLL("avrt.dll")
        avrt.AvRevertMmThreadCharacteristics(ctypes.c_void_p(handle))
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Erreur lors du détachement MMCSS : %s", exc)
