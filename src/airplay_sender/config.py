"""Gestion de la configuration et persistance utilisateur.

Phase 6 du projet AirPlayWindows.
- Modèles Pydantic v2 validant la configuration.
- Stockage par défaut dans %APPDATA%\\airplay-sender\\config.json.
- Écriture atomique (fichier temporaire + os.replace).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

_LOGGER = logging.getLogger("airplay_sender.config")


class DeviceConfig(BaseModel):
    """Informations sur le récepteur AirPlay cible."""

    name: str = ""
    identifier: str = ""
    last_known_ip: str = ""


class CredentialsConfig(BaseModel):
    """Informations d'authentification / appairage."""

    raop: str = ""
    airplay: str = ""


class AudioConfig(BaseModel):
    """Paramètres du pipeline audio."""

    buffer_ms: int = Field(default=800, ge=100, le=5000)
    target_samplerate: int = Field(default=44100)
    capture_device: str = "default-loopback"

    @field_validator("target_samplerate")
    @classmethod
    def validate_samplerate(cls, v: int) -> int:
        if v not in (44100, 48000):
            raise ValueError("target_samplerate doit être 44100 ou 48000 Hz")
        return v


class VolumeConfig(BaseModel):
    """Paramètres de gestion du volume."""

    sync_with_windows: bool = False
    default: float = Field(default=0.3, ge=0.0, le=1.0)


class BehaviorConfig(BaseModel):
    """Paramètres de comportement de l'application."""

    autostart_stream: bool = True
    start_minimized: bool = True
    reconnect: bool = True


class LoggingConfig(BaseModel):
    """Configuration du niveau de log."""

    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        v_upper = v.upper()
        if v_upper not in levels:
            raise ValueError(f"Niveau de log invalide '{v}'. Doit être parmi {levels}")
        return v_upper


class AppConfig(BaseModel):
    """Configuration complète de l'application."""

    device: DeviceConfig = Field(default_factory=DeviceConfig)
    credentials: CredentialsConfig = Field(default_factory=CredentialsConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    volume: VolumeConfig = Field(default_factory=VolumeConfig)
    behavior: BehaviorConfig = Field(default_factory=BehaviorConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def get_default_config_path() -> Path:
    """Retourne l'emplacement standard du fichier de configuration."""
    app_data = os.environ.get("APPDATA")
    if app_data:
        base_dir = Path(app_data)
    else:
        base_dir = Path.home() / ".config"
    return base_dir / "airplay-sender" / "config.json"


def load_config(path: Path | None = None) -> AppConfig:
    """Charge la configuration depuis le disque ou retourne une configuration par défaut."""
    config_path = path or get_default_config_path()

    if not config_path.exists():
        _LOGGER.debug(
            "Fichier de configuration inexistant (%s), utilisation des valeurs par défaut.",
            config_path,
        )
        return AppConfig()

    try:
        content = config_path.read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(content)
        return AppConfig.model_validate(data)
    except Exception as exc:
        _LOGGER.warning(
            "Erreur lors de la lecture du fichier de configuration %s : %s. "
            "Chargement des valeurs par défaut.",
            config_path,
            exc,
        )
        return AppConfig()


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    """Enregistre la configuration de manière atomique sur le disque.

    Écrit d'abord dans un fichier temporaire dans le même dossier puis utilise os.replace
    pour garantir l'intégrité en cas d'extinction brutale.
    """
    config_path = path or get_default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    json_str = config.model_dump_json(indent=2)

    # Écriture dans un fichier temporaire dans le même répertoire pour un déplacement atomique
    temp_fd, temp_path_str = tempfile.mkstemp(
        dir=str(config_path.parent),
        prefix="config_",
        suffix=".tmp",
    )
    temp_path = Path(temp_path_str)

    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(json_str)
        # Remplacement atomique
        os.replace(temp_path, config_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise

    _LOGGER.info("Configuration enregistrée avec succès dans %s", config_path)
    return config_path
