"""Tests unitaires pour l'application System Tray (tray.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from airplay_sender.config import AppConfig, DeviceConfig
from airplay_sender.supervisor import State
from airplay_sender.tray import TrayApp


def test_tray_menu_structure(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    config = AppConfig(
        device=DeviceConfig(name="Phantom Test"),
    )
    app = TrayApp(config, config_path=cfg_file)
    menu = app._create_menu()

    # Vérifie que le menu contient les options requises
    menu_items = list(menu.items)
    item_texts = [str(item.text) for item in menu_items]

    assert any("État" in t for t in item_texts)
    assert any("Démarrer" in t or "Arrêter" in t for t in item_texts)
    assert "Choisir l'enceinte" in item_texts
    assert "Volume" in item_texts
    assert "Démarrer avec Windows" in item_texts
    assert "Ouvrir le dossier des logs" in item_texts
    assert "Copier le rapport de diagnostic" in item_texts
    assert "Quitter" in item_texts


def test_tray_state_change_updates_icon(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    config = AppConfig(device=DeviceConfig(name="Phantom Test"))
    app = TrayApp(config, config_path=cfg_file)

    mock_icon = MagicMock()
    app.icon = mock_icon

    app._on_state_change(State.STREAMING)
    assert app.current_state == State.STREAMING
    assert "STREAMING" in mock_icon.title
    mock_icon.update_menu.assert_called()


def test_tray_volume_change(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    config = AppConfig(device=DeviceConfig(name="Phantom Test"))
    app = TrayApp(config, config_path=cfg_file)

    app._set_volume(0.5)
    assert app.current_volume == 0.5
    assert app.config.volume.default == 0.5
    assert cfg_file.exists()
