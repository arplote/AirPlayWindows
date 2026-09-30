"""Tests unitaires pour l'interface graphique Tkinter (gui.py)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from airplay_sender.config import AppConfig, DeviceConfig
from airplay_sender.gui import AirPlayGUI
from airplay_sender.supervisor import State


@pytest.fixture(scope="module")
def gui_app(tmp_path_factory: pytest.TempPathFactory):
    """Crée une instance unique d'AirPlayGUI pour les tests du module."""
    config = AppConfig(
        device=DeviceConfig(name="Phantom II Test", last_known_ip="192.168.1.42"),
    )
    test_cfg_path = tmp_path_factory.mktemp("cfg") / "gui_config.json"
    with patch("pystray.Icon"):
        app = AirPlayGUI(config, config_path=test_cfg_path, start_minimized=True)
        yield app
        try:
            app.root.destroy()
        except Exception:  # noqa: BLE001
            pass


def test_gui_initial_state(gui_app: AirPlayGUI) -> None:
    """Vérifie l'état initial des composants graphiques."""
    assert gui_app.target_name == "Phantom II Test"
    assert "Phantom II Test" in gui_app.discovered_devices
    assert gui_app.lbl_status_badge["text"] == "● ARRÊTÉ"
    assert "DÉMARRER" in gui_app.btn_toggle["text"]
    assert gui_app.vu_bar["value"] == 0


def test_gui_state_transitions(gui_app: AirPlayGUI) -> None:
    """Vérifie que les transitions d'état mettent à jour l'affichage fidèlement."""
    # 1. STREAMING
    gui_app._update_ui_state(State.STREAMING)
    assert gui_app.lbl_status_badge["text"] == "● DIFFUSION ACTIVE"
    assert "ARRÊTER" in gui_app.btn_toggle["text"]

    # 2. CONNECTING
    gui_app._update_ui_state(State.CONNECTING)
    assert "CONNEXION" in gui_app.lbl_status_badge["text"]

    # 3. DISCOVERING
    gui_app._update_ui_state(State.DISCOVERING)
    assert "RECHERCHE" in gui_app.lbl_status_badge["text"]

    # 4. ERROR
    gui_app._update_ui_state(State.ERROR)
    assert "ERREUR" in gui_app.lbl_status_badge["text"]

    # 5. STOPPED
    gui_app._update_ui_state(State.STOPPED)
    assert gui_app.lbl_status_badge["text"] == "○ ARRÊTÉ"
    assert "DÉMARRER" in gui_app.btn_toggle["text"]


def test_gui_volume_presets(gui_app: AirPlayGUI) -> None:
    """Vérifie le fonctionnement des boutons de présélection du volume."""
    gui_app._set_volume_preset(50)
    assert gui_app.current_volume == 0.50
    assert gui_app.vol_scale.get() == 50.0
    assert "50 %" in gui_app.lbl_volume_val["text"]

    gui_app._set_volume_preset(25)
    assert gui_app.current_volume == 0.25
    assert "25 %" in gui_app.lbl_volume_val["text"]


def test_gui_minimize_to_tray_on_close(gui_app: AirPlayGUI) -> None:
    """Vérifie que [X] masque la fenêtre sans détruire l'application si minimisation activée."""
    gui_app.minimize_to_tray = True
    with patch.object(gui_app.root, "withdraw") as mock_withdraw:
        gui_app._on_window_close()
        mock_withdraw.assert_called_once()


def test_gui_device_selection(gui_app: AirPlayGUI) -> None:
    """Vérifie la sélection d'une nouvelle enceinte."""
    gui_app.discovered_devices = ["Phantom II Test", "Salon Phantom"]
    gui_app._select_device("Salon Phantom")
    assert gui_app.target_name == "Salon Phantom"
    assert gui_app.config.device.name == "Salon Phantom"


def test_gui_latency_profile_selection(gui_app: AirPlayGUI) -> None:
    """Vérifie le changement de profil de latence."""
    gui_app.latency_combo.current(1)  # 350 ms
    gui_app._on_latency_profile_changed()
    assert gui_app.config.audio.buffer_ms == 100
    assert gui_app.config.audio.raop_latency_ms == 350
