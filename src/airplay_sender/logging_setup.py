"""Configuration du logging structuré avec rotation et masquage des secrets.

Phase 5 du projet AirPlayWindows.
- Emplacement : %LOCALAPPDATA%\\airplay-sender\\logs\\app.log
- Rotation : 5 Mo x 5 fichiers
- Masquage des identifiants et secrets dans les logs
- Gestion des niveaux tiers (pyatv, zeroconf).
"""

from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# Regex pour identifier les tokens / secrets / credentials hexadécimaux ou base64
_SECRET_PATTERN = re.compile(
    r"(?i)(credentials|password|secret|pin|token|auth_key)[\s:=]+([a-zA-Z0-9_\-+/=]{6,})"
)


class SensitiveDataFilter(logging.Filter):
    """Filtre de log masquant les credentials sensibles et tokens."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SECRET_PATTERN.sub(r"\1=***", record.msg)
        if record.args:
            if isinstance(record.args, dict):
                clean_args: dict[str, Any] = {}
                for k, v in record.args.items():
                    if isinstance(v, str) and any(
                        s in k.lower() for s in ("secret", "password", "token", "cred")
                    ):
                        clean_args[k] = "***"
                    else:
                        clean_args[k] = v
                record.args = clean_args
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    "***" if (isinstance(arg, str) and len(arg) > 20 and arg.isalnum()) else arg
                    for arg in record.args
                )
        return True


def get_default_log_dir() -> Path:
    """Retourne le répertoire standard de logs sous Windows ou système alternatif."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base_dir = Path(local_app_data)
    else:
        base_dir = Path.home() / ".local" / "share"
    log_dir = base_dir / "airplay-sender" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def setup_logging(
    level: int = logging.INFO,
    verbose: bool = False,
    log_file: Path | None = None,
) -> Path:
    """Configure les gestionnaires de logs (console + fichier rotatif).

    Args:
        level: Niveau de logging de base (ex: logging.INFO).
        verbose: Si True, passe le niveau à DEBUG et pyatv à DEBUG.
        log_file: Chemin optionnel pour le fichier de log.

    Returns:
        Chemin du fichier de log configuré.
    """
    effective_level = logging.DEBUG if verbose else level

    if log_file is None:
        log_dir = get_default_log_dir()
        log_path = log_dir / "app.log"
    else:
        log_path = log_file
        log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(effective_level)

    # Réinitialisation des handlers existants pour éviter les doublons
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    secret_filter = SensitiveDataFilter()

    # Handler fichier rotatif : 5 Mo, 5 archives
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(effective_level)
    file_handler.setFormatter(file_formatter)
    file_handler.addFilter(secret_filter)
    root_logger.addHandler(file_handler)

    # Handler console (stderr)
    console_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(effective_level)
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(secret_filter)
    root_logger.addHandler(console_handler)

    # Configuration des bibliothèques tierces
    third_party_level = logging.DEBUG if verbose else logging.WARNING
    logging.getLogger("pyatv").setLevel(third_party_level)
    logging.getLogger("zeroconf").setLevel(third_party_level)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    root_logger.debug("Système de logs initialisé. Fichier : %s", log_path)
    return log_path
