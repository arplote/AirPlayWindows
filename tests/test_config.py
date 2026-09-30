"""Tests unitaires pour la gestion de la configuration (config.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from airplay_sender.config import (
    AppConfig,
    AudioConfig,
    DeviceConfig,
    LoggingConfig,
    VolumeConfig,
    load_config,
    save_config,
)


def test_default_config() -> None:
    config = AppConfig()
    assert config.device.name == ""
    assert config.audio.buffer_ms == 80
    assert config.audio.raop_latency_ms == 250
    assert config.audio.target_samplerate == 44100
    assert config.volume.default == 0.3
    assert config.logging.level == "INFO"
    assert config.behavior.autostart_stream is True


def test_config_validation_errors() -> None:
    # Volume < 0 ou > 1
    with pytest.raises(ValidationError):
        VolumeConfig(default=1.5)

    with pytest.raises(ValidationError):
        VolumeConfig(default=-0.1)

    # Audio buffer < 30 ms
    with pytest.raises(ValidationError):
        AudioConfig(buffer_ms=20)

    # Taux d'échantillonnage non supporté
    with pytest.raises(ValidationError):
        AudioConfig(target_samplerate=96000)

    # Niveau de log invalide
    with pytest.raises(ValidationError):
        LoggingConfig(level="SUPER_DEBUG")


def test_load_nonexistent_returns_defaults(tmp_path: Path) -> None:
    cfg_file = tmp_path / "nonexistent.json"
    config = load_config(cfg_file)
    assert isinstance(config, AppConfig)
    assert config.device.name == ""


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    cfg_file = tmp_path / "test_config.json"
    original = AppConfig(
        device=DeviceConfig(
            name="Devialet Phantom II",
            identifier="AA:BB:CC:DD:EE:FF",
            last_known_ip="192.168.1.42",
        ),
        audio=AudioConfig(buffer_ms=1000, target_samplerate=44100),
        volume=VolumeConfig(default=0.45),
    )

    saved_path = save_config(original, cfg_file)
    assert saved_path == cfg_file
    assert cfg_file.exists()

    loaded = load_config(cfg_file)
    assert loaded.device.name == "Devialet Phantom II"
    assert loaded.device.identifier == "AA:BB:CC:DD:EE:FF"
    assert loaded.device.last_known_ip == "192.168.1.42"
    assert loaded.audio.buffer_ms == 1000
    assert loaded.volume.default == 0.45


def test_load_corrupted_json_returns_defaults(tmp_path: Path) -> None:
    cfg_file = tmp_path / "corrupted.json"
    cfg_file.write_text("{ this is not valid json }", encoding="utf-8")
    config = load_config(cfg_file)
    assert isinstance(config, AppConfig)
    assert config.audio.buffer_ms == 80


def test_load_legacy_800ms_migrates_to_low_latency(tmp_path: Path) -> None:
    cfg_file = tmp_path / "legacy.json"
    cfg_file.write_text('{"audio": {"buffer_ms": 800}}', encoding="utf-8")
    config = load_config(cfg_file)
    assert config.audio.buffer_ms == 80
    assert config.audio.raop_latency_ms == 250
