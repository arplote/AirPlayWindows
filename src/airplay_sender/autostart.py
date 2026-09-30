"""Gestion du démarrage automatique avec Windows via la base de registre (HKCU).

Phase 7 du projet AirPlayWindows.
Enregistre ou supprime la clé Run dans HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_LOGGER = logging.getLogger("airplay_sender.autostart")

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "AirPlaySender"


def get_launch_command() -> str:
    """Retourne la ligne de commande nécessaire pour démarrer l'application sans console."""
    if getattr(sys, "frozen", False):
        # Exécutable packagé (PyInstaller)
        return f'"{sys.executable}"'

    # Exécution via environnement Python : privilégier pythonw.exe pour ne pas ouvrir de console cmd
    py_dir = Path(sys.executable).parent
    pythonw = py_dir / "pythonw.exe"
    exe_to_use = str(pythonw) if pythonw.exists() else sys.executable
    return f'"{exe_to_use}" -m airplay_sender tray'


def is_autostart_enabled() -> bool:
    """Vérifie si le démarrage automatique est activé dans la base de registre."""
    if sys.platform != "win32":
        return False

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Erreur lors de la lecture du registre autostart : %s", exc)
        return False


def enable_autostart() -> bool:
    """Active le démarrage automatique au démarrage de la session Windows."""
    if sys.platform != "win32":
        return False

    import winreg

    cmd = get_launch_command()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
        _LOGGER.info("Démarrage automatique activé avec la commande : %s", cmd)
        return True
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Impossible d'activer le démarrage automatique : %s", exc)
        return False


def disable_autostart() -> bool:
    """Désactive le démarrage automatique de l'application."""
    if sys.platform != "win32":
        return False

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        _LOGGER.info("Démarrage automatique désactivé.")
        return True
    except FileNotFoundError:
        return True
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Impossible de désactiver le démarrage automatique : %s", exc)
        return False
