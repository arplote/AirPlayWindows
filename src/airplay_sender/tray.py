"""Interface utilisateur dans la zone de notification Windows (System Tray).

Phase 7 du projet AirPlayWindows.
- Icône visuelle à 4 états (gris, orange, vert, rouge).
- Menu contextuel complet : statut, contrôle du streaming, choix de l'enceinte,
  volume, démarrage avec Windows, logs, diagnostic, quitter.
- Notifications discrètes sur perte / rétablissement.
- Exécution du System Tray sur le thread principal et asyncio sur un thread d'arrière-plan.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pystray
from pystray import Menu, MenuItem

from airplay_sender import __version__
from airplay_sender.autostart import disable_autostart, enable_autostart, is_autostart_enabled
from airplay_sender.config import AppConfig, DeviceConfig, save_config
from airplay_sender.diagnostics import generate_diagnostic_report
from airplay_sender.icons import get_icon
from airplay_sender.logging_setup import get_default_log_dir
from airplay_sender.single_instance import SingleInstance
from airplay_sender.supervisor import State, Supervisor

_LOGGER = logging.getLogger("airplay_sender.tray")


def _copy_text_to_clipboard(text: str) -> None:
    """Copie un texte dans le presse-papier Windows sans dépendance externe lourde."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
        _LOGGER.debug("Rapport copié dans le presse-papier via Tkinter.")
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Tkinter indisponible (%s), fallback vers PowerShell...", exc)
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Set-Clipboard", "-Value", "$input"],
                input=text,
                text=True,
                check=False,
            )
        except Exception as ps_exc:  # noqa: BLE001
            _LOGGER.error("Impossible de copier dans le presse-papier : %s", ps_exc)


class TrayApp:
    """Gestionnaire de l'application System Tray Windows."""

    def __init__(self, config: AppConfig, config_path: Path | None = None) -> None:
        self.config = config
        self.config_path = config_path

        self.icon: pystray.Icon | None = None
        self.supervisor: Supervisor | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_thread: threading.Thread | None = None

        self.current_state: State = State.IDLE
        self.target_name: str = self.config.device.name
        self.current_volume: float = self.config.volume.default
        self.discovered_devices: list[str] = [self.target_name] if self.target_name else []

        # Suivi des notifications toasts
        self._reconnecting_start_time: float | None = None
        self._reconnect_toast_sent = False
        self._toast_lock = threading.Lock()

    def start(self) -> int:
        """Démarre l'application et la boucle d'événements du tray."""
        single_instance = SingleInstance()
        if not single_instance.acquire():
            _LOGGER.warning("AirPlay Sender est déjà en cours d'exécution. Fermeture.")
            return 0

        try:
            # Démarrage de la boucle asyncio dans un thread dédié
            self.loop = asyncio.new_event_loop()
            self.loop_thread = threading.Thread(
                target=self._run_asyncio_loop,
                args=(self.loop,),
                name="AsyncioThread",
                daemon=True,
            )
            self.loop_thread.start()

            # Lancer la découverte initiale d'enceintes en arrière-plan
            asyncio.run_coroutine_threadsafe(self._periodic_discovery(), self.loop)

            # Si autostart du streaming activé et appareil configuré, démarrer
            if self.config.behavior.autostart_stream and self.target_name:
                self._start_streaming()

            # Construction et exécution de l'icône pystray
            initial_icon = get_icon(self.current_state.value)
            self.icon = pystray.Icon(
                name="AirPlaySender",
                icon=initial_icon,
                title=f"AirPlay Sender v{__version__} - {self.current_state.value}",
                menu=self._create_menu(),
            )

            _LOGGER.info("Démarrage de l'interface System Tray.")
            self.icon.run()

        finally:
            self._shutdown()
            single_instance.release()

        return 0

    def _run_asyncio_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Exécute la boucle d'événements asyncio jusqu'à l'arrêt."""
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            loop.close()

    def _create_menu(self) -> Menu:
        """Génère dynamiquement les éléments du menu contextuel."""

        def _get_status_text() -> str:
            target = f" ({self.target_name})" if self.target_name else ""
            return f"État : {self.current_state.value}{target}"

        def _get_toggle_text() -> str:
            if self.current_state in (State.STREAMING, State.CONNECTING, State.DISCOVERING):
                return "Arrêter la diffusion"
            return "Démarrer la diffusion"

        def _make_device_item(dev_name: str) -> MenuItem:
            return MenuItem(
                dev_name,
                lambda: self._select_device(dev_name),
                checked=lambda _: dev_name == self.target_name,
            )

        def _make_volume_item(label: str, level: float) -> MenuItem:
            return MenuItem(
                label,
                lambda: self._set_volume(level),
                checked=lambda _: abs(self.current_volume - level) < 0.05,
            )

        # Sous-menu Choix de l'enceinte
        device_items = [_make_device_item(d) for d in sorted(set(self.discovered_devices)) if d]
        if not device_items:
            device_items = [MenuItem("(Aucune enceinte détectée)", None, enabled=False)]

        device_menu = Menu(*device_items)

        # Sous-menu Volume
        volume_menu = Menu(
            _make_volume_item("10 %", 0.10),
            _make_volume_item("25 %", 0.25),
            _make_volume_item("35 %", 0.35),
            _make_volume_item("50 %", 0.50),
            _make_volume_item("75 %", 0.75),
            _make_volume_item("100 %", 1.00),
        )

        return Menu(
            MenuItem(_get_status_text(), None, enabled=False),
            MenuItem(_get_toggle_text(), lambda: self._toggle_streaming()),
            Menu.SEPARATOR,
            MenuItem("Choisir l'enceinte", device_menu),
            MenuItem("Volume", volume_menu),
            Menu.SEPARATOR,
            MenuItem(
                "Démarrer avec Windows",
                lambda: self._toggle_autostart(),
                checked=lambda _: is_autostart_enabled(),
            ),
            MenuItem("Ouvrir le dossier des logs", lambda: self._open_logs_folder()),
            MenuItem("Copier le rapport de diagnostic", lambda: self._copy_diagnostics()),
            Menu.SEPARATOR,
            MenuItem("Quitter", lambda: self._quit()),
        )

    def _on_state_change(self, new_state: State) -> None:
        """Callback invoqué par le superviseur lors d'une transition d'état."""
        self.current_state = new_state
        _LOGGER.debug("Tray notifié de l'état : %s", new_state.value)

        if self.icon:
            self.icon.icon = get_icon(new_state.value)
            self.icon.title = f"AirPlay Sender - {new_state.value}"
            self.icon.update_menu()

        now = time.monotonic()
        with self._toast_lock:
            if new_state == State.RECONNECTING:
                if self._reconnecting_start_time is None:
                    self._reconnecting_start_time = now
                elif now - self._reconnecting_start_time > 10.0 and not self._reconnect_toast_sent:
                    self._reconnect_toast_sent = True
                    self._show_toast(
                        "AirPlay Sender - Connexion interrompue",
                        f"Tentative de reconnexion vers '{self.target_name}'...",
                    )
            elif new_state == State.STREAMING:
                if self._reconnect_toast_sent:
                    self._show_toast(
                        "AirPlay Sender - Connexion rétablie",
                        f"Diffusion du son système active vers '{self.target_name}'.",
                    )
                self._reconnecting_start_time = None
                self._reconnect_toast_sent = False
            elif new_state in (State.IDLE, State.STOPPED):
                self._reconnecting_start_time = None
                self._reconnect_toast_sent = False

    def _show_toast(self, title: str, message: str) -> None:
        """Affiche une notification toast Windows via pystray."""
        try:
            if self.icon and hasattr(self.icon, "notify"):
                self.icon.notify(message, title)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Échec de la notification toast : %s", exc)

    def _start_streaming(self) -> None:
        """Démarre le superviseur dans la boucle asyncio."""
        if not self.target_name or not self.loop:
            return

        self._stop_streaming()

        self.supervisor = Supervisor(
            target_name=self.target_name,
            buffer_ms=self.config.audio.buffer_ms,
            volume=self.current_volume,
            credentials=self.config.credentials.raop or None,
            on_state_change=self._on_state_change,
        )
        asyncio.run_coroutine_threadsafe(self.supervisor.run(), self.loop)

    def _stop_streaming(self) -> None:
        """Arrête proprement le superviseur."""
        if self.supervisor:
            self.supervisor.request_stop()
            self.supervisor = None
        self._on_state_change(State.STOPPED)

    def _toggle_streaming(self) -> None:
        """Bascule entre démarrage et arrêt de la diffusion."""
        if self.current_state in (State.STREAMING, State.CONNECTING, State.DISCOVERING):
            self._stop_streaming()
        else:
            self._start_streaming()

    def _select_device(self, dev_name: str) -> None:
        """Change l'enceinte sélectionnée et relance le flux si actif."""
        if dev_name == self.target_name:
            return

        _LOGGER.info("Sélection d'une nouvelle enceinte cible : %s", dev_name)
        self.target_name = dev_name
        self.config.device = DeviceConfig(name=dev_name)
        save_config(self.config, self.config_path)

        if self.current_state in (State.STREAMING, State.CONNECTING, State.DISCOVERING):
            self._start_streaming()
        elif self.icon:
            self.icon.update_menu()

    def _set_volume(self, level: float) -> None:
        """Modifie le volume de diffusion."""
        self.current_volume = level
        self.config.volume.default = level
        save_config(self.config, self.config_path)

        if self.supervisor and self.loop:
            asyncio.run_coroutine_threadsafe(
                self.supervisor.set_volume(level),
                self.loop,
            )
        if self.icon:
            self.icon.update_menu()

    def _toggle_autostart(self) -> None:
        """Active ou désactive le démarrage automatique avec Windows."""
        if is_autostart_enabled():
            disable_autostart()
        else:
            enable_autostart()
        if self.icon:
            self.icon.update_menu()

    def _open_logs_folder(self) -> None:
        """Ouvre le répertoire de logs dans l'Explorateur Windows."""
        log_dir = get_default_log_dir()
        if sys.platform == "win32":
            os.startfile(str(log_dir))  # type: ignore[attr-defined]

    def _copy_diagnostics(self) -> None:
        """Génère le rapport de diagnostic et le copie dans le presse-papier."""
        if not self.loop:
            return

        async def _run_and_copy() -> None:
            report = await generate_diagnostic_report(self.config_path)
            _copy_text_to_clipboard(report)
            self._show_toast(
                "Diagnostic copié",
                "Le rapport a été copié dans le presse-papier Windows.",
            )

        asyncio.run_coroutine_threadsafe(_run_and_copy(), self.loop)

    async def _periodic_discovery(self) -> None:
        """Sonde périodiquement le réseau local pour découvrir de nouvelles enceintes."""
        import pyatv

        while True:
            try:
                loop = asyncio.get_running_loop()
                results = await pyatv.scan(loop, timeout=3)
                names = [r.name for r in results if r.name]
                if names:
                    updated = False
                    for n in names:
                        if n not in self.discovered_devices:
                            self.discovered_devices.append(n)
                            updated = True
                    if updated and self.icon:
                        self.icon.update_menu()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Erreur lors de la découverte d'enceintes en arrière-plan : %s", exc)

            await asyncio.sleep(60.0)

    def _quit(self) -> None:
        """Arrête complètement l'application."""
        _LOGGER.info("Fermeture demandée par l'utilisateur.")
        self._stop_streaming()
        if self.icon:
            self.icon.stop()

    def _shutdown(self) -> None:
        """Libère les ressources d'arrière-plan."""
        self._stop_streaming()
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.loop_thread and self.loop_thread.is_alive():
            self.loop_thread.join(timeout=2.0)


def run_tray_app(config: AppConfig, config_path: Path | None = None) -> int:
    """Point d'entrée pour lancer l'application en mode System Tray."""
    app = TrayApp(config, config_path=config_path)
    return app.start()
