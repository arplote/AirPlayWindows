"""Interface graphique principale (GUI) sous Tkinter et intégration System Tray.

Fenêtre moderne sous Windows 11 offrant :
- Carte de statut visuelle en temps réel (état du superviseur).
- Bouton principal de démarrage / arrêt de la diffusion.
- Sélection de l'enceinte cible avec détection mDNS automatique.
- Contrôle de volume interactif (curseur et boutons rapides).
- Vu-mètre audio en direct (WASAPI Loopback) pour visualiser la capture du son PC.
- Paramètres Windows (démarrage avec Windows, réduction dans le Tray).
- Accès direct aux logs et au rapport de diagnostic.
- Double intégration : Fenêtre active + Icône dans la barre des tâches (System Tray).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

import pystray
from pystray import Menu, MenuItem

from airplay_sender import __version__
from airplay_sender.autostart import disable_autostart, enable_autostart, is_autostart_enabled
from airplay_sender.config import AppConfig, DeviceConfig, save_config
from airplay_sender.diagnostics import generate_diagnostic_report
from airplay_sender.icons import get_icon
from airplay_sender.logging_setup import get_default_log_dir
from airplay_sender.mmcss import disable_mmcss, enable_mmcss
from airplay_sender.single_instance import SingleInstance
from airplay_sender.supervisor import State, Supervisor

_LOGGER = logging.getLogger("airplay_sender.gui")


# Configuration de l'environnement Tcl/Tk sous Windows si nécessaire
if sys.platform == "win32":
    tcl_base = Path(sys.base_prefix) / "tcl"
    if "TCL_LIBRARY" not in os.environ and (tcl_base / "tcl8.6").exists():
        os.environ["TCL_LIBRARY"] = str(tcl_base / "tcl8.6")
    if "TK_LIBRARY" not in os.environ and (tcl_base / "tk8.6").exists():
        os.environ["TK_LIBRARY"] = str(tcl_base / "tk8.6")


class AirPlayGUI:
    """Fenêtre principale de l'application AirPlay Sender avec support System Tray."""

    def __init__(
        self,
        config: AppConfig,
        config_path: Path | None = None,
        start_minimized: bool = False,
        root: Any | None = None,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.start_minimized = start_minimized

        self.supervisor: Supervisor | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_thread: threading.Thread | None = None
        self.tray_icon: pystray.Icon | None = None

        self.current_state: State = State.IDLE
        self.target_name: str = self.config.device.name
        self.current_volume: float = self.config.volume.default
        self.discovered_devices: list[str] = [self.target_name] if self.target_name else []

        self.minimize_to_tray: bool = getattr(self.config.behavior, "minimize_to_tray", True)
        self._is_scanning = False
        self._volume_update_job: str | None = None
        self._toast_shown_once = False

        # Imports Tkinter
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk

        self.root = root or tk.Tk()
        self._setup_window()
        self._build_ui()
        self._fit_window_to_content()

    def _fit_window_to_content(self) -> None:
        """Ajuste la hauteur de la fenêtre au contenu (selon l'échelle d'affichage)."""
        self.root.update_idletasks()
        width = max(self.root.winfo_width(), self.root.winfo_reqwidth())
        height = self.root.winfo_reqheight() + 8
        max_height = self.root.winfo_screenheight() - 80
        height = min(height, max_height)
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = max((self.root.winfo_screenheight() - height) // 2 - 20, 0)
        self.root.minsize(width - 40, min(height, 620))
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _setup_window(self) -> None:
        """Configure les paramètres de base de la fenêtre Tkinter."""
        self.root.title("AirPlay Sender")
        self.root.geometry("520x680")
        self.root.minsize(480, 620)
        self.root.configure(bg="#F4F5F7")

        # Recherche robuste de l'icône de la fenêtre
        possible_icon_paths = [
            Path(__file__).resolve().parent.parent.parent / "assets" / "icon.ico",
            Path(sys.executable).parent / "assets" / "icon.ico",
            Path(getattr(sys, "_MEIPASS", "")) / "assets" / "icon.ico",
        ]
        for p in possible_icon_paths:
            if p.exists():
                try:
                    self.root.iconbitmap(str(p))
                    break
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("Impossible de charger l'icône .ico depuis %s : %s", p, exc)

        # Centrage sur l'écran
        self.root.update_idletasks()
        width = 520
        height = 680
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        # Interception de la fermeture [X]
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

    def _build_ui(self) -> None:
        """Construit l'interface graphique avec un style moderne."""
        tk = self.tk
        ttk = self.ttk

        # Configuration des styles TTK
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass

        style.configure("TFrame", background="#F4F5F7")
        style.configure("Card.TFrame", background="#FFFFFF", relief="flat")
        style.configure(
            "Header.TLabel",
            font=("Segoe UI", 16, "bold"),
            background="#FFFFFF",
            foreground="#1E293B",
        )
        style.configure(
            "SubHeader.TLabel", font=("Segoe UI", 9), background="#FFFFFF", foreground="#64748B"
        )
        style.configure(
            "CardTitle.TLabel",
            font=("Segoe UI", 11, "bold"),
            background="#FFFFFF",
            foreground="#0F172A",
        )
        style.configure(
            "Regular.TLabel", font=("Segoe UI", 10), background="#FFFFFF", foreground="#334155"
        )
        style.configure(
            "Hint.TLabel", font=("Segoe UI", 8), background="#FFFFFF", foreground="#64748B"
        )

        # Style pour le Vu-mètre
        style.configure(
            "AudioMeter.Horizontal.TProgressbar",
            troughcolor="#E2E8F0",
            background="#10B981",
            thickness=16,
        )

        main_canvas_frame = tk.Frame(self.root, bg="#F4F5F7")
        main_canvas_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        # -------------------------------------------------------------
        # CARTE 1 : EN-TÊTE ET STATUT DE DIFFUSION
        # -------------------------------------------------------------
        card_header = tk.Frame(
            main_canvas_frame, bg="#FFFFFF", highlightbackground="#E2E8F0", highlightthickness=1
        )
        card_header.pack(fill=tk.X, pady=(0, 10))

        header_inner = tk.Frame(card_header, bg="#FFFFFF", padx=16, pady=14)
        header_inner.pack(fill=tk.X)

        title_row = tk.Frame(header_inner, bg="#FFFFFF")
        title_row.pack(fill=tk.X)

        lbl_app_name = tk.Label(
            title_row,
            text="AirPlay Sender",
            font=("Segoe UI", 15, "bold"),
            bg="#FFFFFF",
            fg="#0F172A",
        )
        lbl_app_name.pack(side=tk.LEFT)

        lbl_version = tk.Label(
            title_row,
            text=f"v{__version__}",
            font=("Segoe UI", 9),
            bg="#F1F5F9",
            fg="#475569",
            padx=6,
            pady=2,
        )
        lbl_version.pack(side=tk.LEFT, padx=(8, 0))

        # Badge de statut (pill)
        self.lbl_status_badge = tk.Label(
            title_row,
            text="● ARRÊTÉ",
            font=("Segoe UI", 9, "bold"),
            bg="#F1F5F9",
            fg="#64748B",
            padx=10,
            pady=3,
        )
        self.lbl_status_badge.pack(side=tk.RIGHT)

        lbl_desc = tk.Label(
            header_inner,
            text="Diffuse tout le son de votre PC Windows vers votre enceinte AirPlay.",
            font=("Segoe UI", 9),
            bg="#FFFFFF",
            fg="#64748B",
        )
        lbl_desc.pack(anchor="w", pady=(4, 12))

        # Grand bouton interactif de diffusion
        self.btn_toggle = tk.Button(
            header_inner,
            text="▶  DÉMARRER LA DIFFUSION",
            font=("Segoe UI", 11, "bold"),
            bg="#10B981",
            fg="#FFFFFF",
            activebackground="#059669",
            activeforeground="#FFFFFF",
            relief="flat",
            cursor="hand2",
            pady=8,
            command=self._on_toggle_streaming,
        )
        self.btn_toggle.pack(fill=tk.X)

        # -------------------------------------------------------------
        # CARTE 2 : VU-MÈTRE AUDIO EN DIRECT
        # -------------------------------------------------------------
        card_audio = tk.Frame(
            main_canvas_frame, bg="#FFFFFF", highlightbackground="#E2E8F0", highlightthickness=1
        )
        card_audio.pack(fill=tk.X, pady=(0, 10))

        audio_inner = tk.Frame(card_audio, bg="#FFFFFF", padx=16, pady=12)
        audio_inner.pack(fill=tk.X)

        audio_title_row = tk.Frame(audio_inner, bg="#FFFFFF")
        audio_title_row.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            audio_title_row,
            text="Signal Audio Windows (Loopback)",
            font=("Segoe UI", 10, "bold"),
            bg="#FFFFFF",
            fg="#0F172A",
        ).pack(side=tk.LEFT)
        self.lbl_audio_peak = tk.Label(
            audio_title_row,
            text="Inactif (0 %)",
            font=("Segoe UI", 9, "bold"),
            bg="#FFFFFF",
            fg="#64748B",
        )
        self.lbl_audio_peak.pack(side=tk.RIGHT)

        # Barre de niveau (VU-mètre)
        self.vu_bar = ttk.Progressbar(
            audio_inner,
            orient="horizontal",
            mode="determinate",
            style="AudioMeter.Horizontal.TProgressbar",
            maximum=100,
        )
        self.vu_bar.pack(fill=tk.X, pady=(2, 6))

        audio_footer_row = tk.Frame(audio_inner, bg="#FFFFFF")
        audio_footer_row.pack(fill=tk.X)

        self.lbl_buffer_info = tk.Label(
            audio_footer_row, text="Tampon : 0 ms", font=("Segoe UI", 8), bg="#FFFFFF", fg="#64748B"
        )
        self.lbl_buffer_info.pack(side=tk.LEFT)

        self.lbl_audio_hint = tk.Label(
            audio_footer_row,
            text="Lancez un son sur votre PC pour tester",
            font=("Segoe UI", 8, "italic"),
            bg="#FFFFFF",
            fg="#94A3B8",
        )
        self.lbl_audio_hint.pack(side=tk.RIGHT)

        self.lbl_routing_tip = tk.Label(
            audio_inner,
            text="🎧 Sortie : Initialisation de la source audio...",
            font=("Segoe UI", 8),
            bg="#FFFFFF",
            fg="#64748B",
            wraplength=460,
            justify=tk.LEFT,
        )
        self.lbl_routing_tip.pack(anchor="w", pady=(6, 0))

        # -------------------------------------------------------------
        # CARTE 3 : ENCEINTE CIBLE
        # -------------------------------------------------------------
        card_device = tk.Frame(
            main_canvas_frame, bg="#FFFFFF", highlightbackground="#E2E8F0", highlightthickness=1
        )
        card_device.pack(fill=tk.X, pady=(0, 10))

        dev_inner = tk.Frame(card_device, bg="#FFFFFF", padx=16, pady=12)
        dev_inner.pack(fill=tk.X)

        tk.Label(
            dev_inner,
            text="Enceinte cible AirPlay / RAOP",
            font=("Segoe UI", 10, "bold"),
            bg="#FFFFFF",
            fg="#0F172A",
        ).pack(anchor="w", pady=(0, 6))

        dev_row = tk.Frame(dev_inner, bg="#FFFFFF")
        dev_row.pack(fill=tk.X)

        self.device_combo = ttk.Combobox(
            dev_row,
            values=self.discovered_devices,
            state="readonly",
            font=("Segoe UI", 9),
        )
        if self.target_name and self.target_name in self.discovered_devices:
            self.device_combo.set(self.target_name)
        elif self.discovered_devices:
            self.device_combo.set(self.discovered_devices[0])
        self.device_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)

        self.btn_refresh = tk.Button(
            dev_row,
            text="🔄 Actualiser",
            font=("Segoe UI", 9),
            bg="#F1F5F9",
            fg="#1E293B",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=4,
            command=self._on_refresh_devices,
        )
        self.btn_refresh.pack(side=tk.RIGHT)

        # -------------------------------------------------------------
        # CARTE 4 : VOLUME DE L'ENCEINTE
        # -------------------------------------------------------------
        card_vol = tk.Frame(
            main_canvas_frame, bg="#FFFFFF", highlightbackground="#E2E8F0", highlightthickness=1
        )
        card_vol.pack(fill=tk.X, pady=(0, 10))

        vol_inner = tk.Frame(card_vol, bg="#FFFFFF", padx=16, pady=12)
        vol_inner.pack(fill=tk.X)

        vol_title_row = tk.Frame(vol_inner, bg="#FFFFFF")
        vol_title_row.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            vol_title_row,
            text="Volume de diffusion",
            font=("Segoe UI", 10, "bold"),
            bg="#FFFFFF",
            fg="#0F172A",
        ).pack(side=tk.LEFT)
        initial_vol_pct = int(self.current_volume * 100)
        self.lbl_volume_val = tk.Label(
            vol_title_row,
            text=f"{initial_vol_pct} %",
            font=("Segoe UI", 9, "bold"),
            bg="#FFFFFF",
            fg="#0284C7",
        )
        self.lbl_volume_val.pack(side=tk.RIGHT)

        self.vol_scale = ttk.Scale(
            vol_inner,
            from_=0,
            to=100,
            orient="horizontal",
            command=self._on_volume_slider_changed,
        )
        self.vol_scale.set(initial_vol_pct)
        self.vol_scale.pack(fill=tk.X, pady=(2, 8))

        # Boutons rapides de volume
        preset_row = tk.Frame(vol_inner, bg="#FFFFFF")
        preset_row.pack(fill=tk.X)

        for pct in (15, 30, 50, 75):
            btn = tk.Button(
                preset_row,
                text=f"{pct}%",
                font=("Segoe UI", 8),
                bg="#F8FAFC",
                fg="#334155",
                relief="flat",
                cursor="hand2",
                padx=8,
                pady=2,
                command=lambda p=pct: self._set_volume_preset(p),
            )
            btn.pack(side=tk.LEFT, padx=(0, 6))

        # -------------------------------------------------------------
        # CARTE 5 : OPTIONS ET OUTILS
        # -------------------------------------------------------------
        card_opts = tk.Frame(
            main_canvas_frame, bg="#FFFFFF", highlightbackground="#E2E8F0", highlightthickness=1
        )
        card_opts.pack(fill=tk.X, pady=(0, 10))

        opts_inner = tk.Frame(card_opts, bg="#FFFFFF", padx=16, pady=10)
        opts_inner.pack(fill=tk.X)

        self.var_autostart = tk.BooleanVar(value=is_autostart_enabled())
        chk_autostart = tk.Checkbutton(
            opts_inner,
            text="Démarrer automatiquement avec Windows",
            variable=self.var_autostart,
            command=self._on_toggle_autostart,
            font=("Segoe UI", 9),
            bg="#FFFFFF",
            fg="#1E293B",
            activebackground="#FFFFFF",
        )
        chk_autostart.pack(anchor="w", pady=(0, 4))

        self.var_min_tray = tk.BooleanVar(value=self.minimize_to_tray)
        chk_min_tray = tk.Checkbutton(
            opts_inner,
            text="Réduire dans la zone de notification lors de la fermeture [X]",
            variable=self.var_min_tray,
            command=self._on_toggle_min_tray,
            font=("Segoe UI", 9),
            bg="#FFFFFF",
            fg="#1E293B",
            activebackground="#FFFFFF",
        )
        chk_min_tray.pack(anchor="w", pady=(0, 6))

        # Choix du profil de latence
        latency_row = tk.Frame(opts_inner, bg="#FFFFFF")
        latency_row.pack(fill=tk.X, pady=(2, 6))

        tk.Label(
            latency_row,
            text="Profil de latence :",
            font=("Segoe UI", 9, "bold"),
            bg="#FFFFFF",
            fg="#1E293B",
        ).pack(side=tk.LEFT)

        self.latency_combo = ttk.Combobox(
            latency_row,
            values=[
                "⚡ Ultra-faible (250 ms - Recommandé)",
                "⚖️ Équilibré (350 ms)",
                "🛡️ Haute stabilité (800 ms)",
            ],
            state="readonly",
            font=("Segoe UI", 8),
            width=32,
        )
        # Sélection du profil actuel
        cur_raop = getattr(self.config.audio, "raop_latency_ms", 250)
        if cur_raop <= 250:
            self.latency_combo.current(0)
        elif cur_raop <= 450:
            self.latency_combo.current(1)
        else:
            self.latency_combo.current(2)

        self.latency_combo.pack(side=tk.RIGHT)
        self.latency_combo.bind("<<ComboboxSelected>>", self._on_latency_profile_changed)

        capture_row = tk.Frame(opts_inner, bg="#FFFFFF")
        capture_row.pack(fill=tk.X, pady=(2, 6))

        tk.Label(
            capture_row,
            text="Sortie Windows capturée :",
            font=("Segoe UI", 9, "bold"),
            bg="#FFFFFF",
            fg="#1E293B",
        ).pack(side=tk.LEFT)

        self.capture_combo = ttk.Combobox(
            capture_row,
            state="readonly",
            font=("Segoe UI", 8),
            width=32,
            postcommand=self._fill_capture_combo,
        )
        self._fill_capture_combo()
        current_capture = getattr(self.config.audio, "capture_device", "default-loopback")
        if current_capture in ("", "auto", "default-loopback"):
            self.capture_combo.set(self._CAPTURE_AUTO_LABEL)
        else:
            self.capture_combo.set(current_capture)
        self.capture_combo.pack(side=tk.RIGHT)
        self.capture_combo.bind("<<ComboboxSelected>>", self._on_capture_device_changed)

        tools_row = tk.Frame(opts_inner, bg="#FFFFFF")
        tools_row.pack(fill=tk.X, pady=(8, 0))

        btn_logs = tk.Button(
            tools_row,
            text="📁 Dossier des logs",
            font=("Segoe UI", 8),
            bg="#F1F5F9",
            fg="#1E293B",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=4,
            command=self._open_logs_folder,
        )
        btn_logs.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_diag = tk.Button(
            tools_row,
            text="📋 Copier le diagnostic",
            font=("Segoe UI", 8),
            bg="#F1F5F9",
            fg="#1E293B",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=4,
            command=self._copy_diagnostic,
        )
        self.btn_diag.pack(side=tk.LEFT)

        # -------------------------------------------------------------
        # BARRE BAS : QUITTER
        # -------------------------------------------------------------
        bottom_frame = tk.Frame(main_canvas_frame, bg="#F4F5F7")
        bottom_frame.pack(fill=tk.X, pady=(6, 0))

        btn_quit = tk.Button(
            bottom_frame,
            text="Fermer et quitter complètement l'application",
            font=("Segoe UI", 8),
            bg="#F4F5F7",
            fg="#94A3B8",
            activebackground="#F4F5F7",
            activeforeground="#DC2626",
            relief="flat",
            cursor="hand2",
            command=self.quit_app,
        )
        btn_quit.pack(side=tk.RIGHT)

    def start(self) -> int:
        """Démarre l'application GUI avec la boucle Tkinter et les threads d'arrière-plan."""
        single_instance = SingleInstance()
        if not single_instance.acquire():
            _LOGGER.warning("Une autre instance d'AirPlay Sender tourne déjà.")
            # Tentative de ramener la fenêtre existante au premier plan
            self._try_restore_existing_window()
            return 0

        try:
            # 1. Démarrage de la boucle asyncio en arrière-plan
            self.loop = asyncio.new_event_loop()
            self.loop_thread = threading.Thread(
                target=self._run_asyncio_loop,
                args=(self.loop,),
                name="AsyncioThread",
                daemon=True,
            )
            self.loop_thread.start()

            # 2. Démarrage de la détection périodique des enceintes
            asyncio.run_coroutine_threadsafe(self._periodic_discovery(), self.loop)

            # 3. Démarrage du System Tray en mode détaché
            self._init_tray_icon()

            # 4. Si autostart du streaming activé et cible définie, démarrer
            if self.config.behavior.autostart_stream and self.target_name:
                self._start_streaming()

            # 5. Gestion de l'affichage initial de la fenêtre
            if self.start_minimized:
                self.root.withdraw()
            else:
                self.root.deiconify()

            # 6. Démarrage de la boucle de rafraîchissement du vu-mètre (50 ms / 20 fps)
            self.root.after(50, self._tick_audio_meter)

            # 7. Boucle principale Tkinter
            self.root.mainloop()

        finally:
            self._shutdown()
            single_instance.release()

        return 0

    def _run_asyncio_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Exécute la boucle d'événements asyncio (pacing RTP inclus)."""
        asyncio.set_event_loop(loop)
        mmcss_handle = enable_mmcss("Pro Audio")
        try:
            loop.run_forever()
        finally:
            disable_mmcss(mmcss_handle)
            loop.close()

    def _init_tray_icon(self) -> None:
        """Initialise l'icône dans la zone de notification Windows."""
        initial_img = get_icon(self.current_state.value)
        self.tray_icon = pystray.Icon(
            name="AirPlaySender",
            icon=initial_img,
            title=f"AirPlay Sender v{__version__} - {self.current_state.value}",
            menu=self._create_tray_menu(),
        )
        self.tray_icon.run_detached()

    def _create_tray_menu(self) -> Menu:
        """Crée le menu contextuel du System Tray."""

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
                lambda: self._set_volume_preset(int(level * 100)),
                checked=lambda _: abs(self.current_volume - level) < 0.05,
            )

        device_items = [_make_device_item(d) for d in sorted(set(self.discovered_devices)) if d]
        if not device_items:
            device_items = [MenuItem("(Aucune enceinte détectée)", None, enabled=False)]

        device_menu = Menu(*device_items)

        volume_menu = Menu(
            _make_volume_item("15 %", 0.15),
            _make_volume_item("30 %", 0.30),
            _make_volume_item("50 %", 0.50),
            _make_volume_item("75 %", 0.75),
            _make_volume_item("100 %", 1.00),
        )

        return Menu(
            MenuItem("Ouvrir AirPlay Sender", lambda: self.show_window(), default=True),
            Menu.SEPARATOR,
            MenuItem(_get_status_text(), None, enabled=False),
            MenuItem(_get_toggle_text(), lambda: self.root.after(0, self._on_toggle_streaming)),
            Menu.SEPARATOR,
            MenuItem("Choisir l'enceinte", device_menu),
            MenuItem("Volume", volume_menu),
            Menu.SEPARATOR,
            MenuItem("Quitter", lambda: self.root.after(0, self.quit_app)),
        )

    def show_window(self) -> None:
        """Affiche la fenêtre Tkinter et la place au premier plan."""
        self.root.after(0, self._bring_to_front)

    def _bring_to_front(self) -> None:
        """Code d'élévation de la fenêtre sur le thread Tkinter."""
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after_idle(self.root.attributes, "-topmost", False)
        self.root.focus_force()

    def _on_window_close(self) -> None:
        """Gère la fermeture de la fenêtre [X]."""
        if self.minimize_to_tray:
            self.root.withdraw()
            if not self._toast_shown_once and self.tray_icon and hasattr(self.tray_icon, "notify"):
                self._toast_shown_once = True
                try:
                    self.tray_icon.notify(
                        "AirPlay Sender continue de fonctionner dans la barre des tâches.",
                        "AirPlay Sender",
                    )
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("Erreur notification toast : %s", exc)
        else:
            self.quit_app()

    def _on_toggle_min_tray(self) -> None:
        """Active ou désactive la réduction en zone de notification."""
        self.minimize_to_tray = self.var_min_tray.get()
        self.config.behavior.start_minimized = self.minimize_to_tray
        save_config(self.config, self.config_path)

    def _on_toggle_autostart(self) -> None:
        """Bascule le démarrage automatique avec Windows."""
        if self.var_autostart.get():
            enable_autostart()
        else:
            disable_autostart()
        if self.tray_icon:
            self.tray_icon.update_menu()

    _CAPTURE_AUTO_LABEL = "Automatique (CABLE Input en priorité)"

    def _fill_capture_combo(self) -> None:
        """Liste les sorties Windows capturables (rafraîchie à chaque ouverture du menu)."""
        from airplay_sender.capture import list_wasapi_loopback_devices

        try:
            names = [d["name"] for d in list_wasapi_loopback_devices()]
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Liste des sorties audio indisponible : %s", exc)
            names = []
        current = self.capture_combo.get()
        if current and current != self._CAPTURE_AUTO_LABEL and current not in names:
            names.append(current)
        self.capture_combo["values"] = [self._CAPTURE_AUTO_LABEL, *names]

    def _on_capture_device_changed(self, _event: Any = None) -> None:
        """Enregistre la sortie à capturer et redémarre la capture si le flux est actif."""
        choice = self.capture_combo.get()
        value = "default-loopback" if choice == self._CAPTURE_AUTO_LABEL else choice
        self.config.audio.capture_device = value
        save_config(self.config, self.config_path)
        _LOGGER.info("Sortie capturée définie sur : %s", value)

        if self.supervisor:
            self.supervisor.capture_device = value
            self.supervisor._restart_capture()

    def _on_latency_profile_changed(self, _event: Any = None) -> None:
        """Applique le nouveau profil de latence sélectionné."""
        idx = self.latency_combo.current()
        if idx == 0:
            buf_ms, raop_ms = 80, 250
        elif idx == 1:
            buf_ms, raop_ms = 100, 350
        else:
            buf_ms, raop_ms = 150, 750

        self.config.audio.buffer_ms = buf_ms
        self.config.audio.raop_latency_ms = raop_ms
        save_config(self.config, self.config_path)

        if self.supervisor:
            self.supervisor.set_latency(buf_ms, raop_ms)
        _LOGGER.info(
            "Profil de latence appliqué : buffer=%d ms, raop=%d ms", buf_ms, raop_ms
        )

    def _tick_audio_meter(self) -> None:
        """Mise à jour en continu (20 FPS) du Vu-mètre audio et des informations de flux."""
        try:
            if self.supervisor and self.current_state == State.STREAMING:
                peak = self.supervisor.get_audio_level()
                pct = int(peak * 100)
                self.vu_bar["value"] = pct

                is_virt = self.supervisor.is_capture_virtual
                cap_name = self.supervisor.capture_device_name

                if is_virt:
                    self.lbl_routing_tip.config(
                        text=(
                            "🎧 Sortie dédiée (Câble Virtuel) : Choisissez 'CABLE Input' "
                            "dans Windows pour jouer sur la Devialet (PC muet), "
                            "ou 'Haut-parleurs' pour le PC."
                        ),
                        fg="#059669",
                    )
                else:
                    self.lbl_routing_tip.config(
                        text=(
                            f"💻 Sortie miroir : {cap_name}\n"
                            "Baissez le son PC pour éviter l'écho, ou lancez "
                            "'Installer_VB-CABLE.bat' pour la sortie dédiée."
                        ),
                        fg="#64748B",
                    )

                if peak > 0.02:
                    self.lbl_audio_peak.config(text=f"Signal actif ({pct} %)", fg="#10B981")
                    self.lbl_audio_hint.config(
                        text="Capture Câble Virtuel" if is_virt else "Capture son PC",
                        fg="#10B981",
                    )
                else:
                    self.lbl_audio_peak.config(text="Silence (0 %)", fg="#64748B")
                    self.lbl_audio_hint.config(text="En attente de son", fg="#94A3B8")

                if self.supervisor.pipeline:
                    lvl_ms = int(self.supervisor.pipeline.level_ms)
                    lat_est = int(self.supervisor.raop_latency_ms + lvl_ms)
                    self.lbl_buffer_info.config(
                        text=f"Tampon : {lvl_ms} ms | Latence est. : ~{lat_est} ms"
                    )

                # Synchronisation dynamique du curseur de volume avec les gestes Windows / Touchpad
                if self.supervisor.volume is not None and not self._volume_update_job:
                    win_pct = int(self.supervisor.volume * 100)
                    if abs(int(self.vol_scale.get()) - win_pct) >= 1:
                        self.vol_scale.set(win_pct)
                        self.lbl_volume_val.config(text=f"{win_pct} %")
                        self.current_volume = self.supervisor.volume
            else:
                self.vu_bar["value"] = 0
                if self.current_state == State.CONNECTING:
                    self.lbl_audio_peak.config(text="Connexion...", fg="#D97706")
                    self.lbl_audio_hint.config(text="Établissement du flux RAOP", fg="#D97706")
                else:
                    self.lbl_audio_peak.config(text="Inactif (0 %)", fg="#64748B")
                    self.lbl_audio_hint.config(text="Diffusion arrêtée", fg="#94A3B8")
                self.lbl_buffer_info.config(text="Tampon : 0 ms")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Erreur rafraîchissement audio vu-meter : %s", exc)

        # Planifier la prochaine mesure dans 50 millisecondes
        self.root.after(50, self._tick_audio_meter)

    def _on_state_change(self, new_state: State) -> None:
        """Callback invoqué par le superviseur lors d'une transition d'état."""
        self.current_state = new_state
        _LOGGER.debug("Notification transition d'état : %s", new_state.value)

        # Mise à jour du tray
        if self.tray_icon:
            self.tray_icon.icon = get_icon(new_state.value)
            self.tray_icon.title = f"AirPlay Sender - {new_state.value}"
            self.tray_icon.update_menu()

        # Mise à jour de la fenêtre sur le thread Tkinter
        self.root.after(0, lambda: self._update_ui_state(new_state))

    def _update_ui_state(self, state: State) -> None:
        """Met à jour les contrôles visuels de la fenêtre selon l'état."""
        if state == State.STREAMING:
            self.lbl_status_badge.config(
                text="● DIFFUSION ACTIVE",
                bg="#ECFDF5",
                fg="#059669",
            )
            self.btn_toggle.config(
                text="⏹  ARRÊTER LA DIFFUSION",
                bg="#EF4444",
                activebackground="#DC2626",
            )
        elif state in (State.CONNECTING, State.DISCOVERING, State.RECONNECTING):
            txt = "● CONNEXION..." if state != State.DISCOVERING else "● RECHERCHE..."
            self.lbl_status_badge.config(
                text=txt,
                bg="#FFFBEB",
                fg="#D97706",
            )
            self.btn_toggle.config(
                text="⏹  ARRÊTER LA CONNEXION",
                bg="#EF4444",
                activebackground="#DC2626",
            )
        elif state == State.ERROR:
            self.lbl_status_badge.config(
                text="● ERREUR",
                bg="#FEF2F2",
                fg="#DC2626",
            )
            self.btn_toggle.config(
                text="▶  RÉESSAYER LA DIFFUSION",
                bg="#10B981",
                activebackground="#059669",
            )
        else:  # IDLE, STOPPED
            self.lbl_status_badge.config(
                text="○ ARRÊTÉ",
                bg="#F1F5F9",
                fg="#64748B",
            )
            self.btn_toggle.config(
                text="▶  DÉMARRER LA DIFFUSION",
                bg="#10B981",
                activebackground="#059669",
            )

    def _on_toggle_streaming(self) -> None:
        """Déclenche le démarrage ou l'arrêt de la diffusion."""
        if self.current_state in (
            State.STREAMING,
            State.CONNECTING,
            State.DISCOVERING,
            State.RECONNECTING,
        ):
            self._stop_streaming()
        else:
            self._start_streaming()

    def _start_streaming(self) -> None:
        """Démarre le streaming vers l'enceinte sélectionnée."""
        if not self.target_name or not self.loop:
            return

        self._stop_streaming()

        self.supervisor = Supervisor(
            target_name=self.target_name,
            buffer_ms=self.config.audio.buffer_ms,
            raop_latency_ms=getattr(self.config.audio, "raop_latency_ms", 250),
            volume=self.current_volume,
            credentials=self.config.credentials.raop or None,
            on_state_change=self._on_state_change,
            prefer_virtual_cable=getattr(self.config.audio, "prefer_virtual_cable", True),
            capture_device=self.config.audio.capture_device,
        )
        asyncio.run_coroutine_threadsafe(self.supervisor.run(), self.loop)

    def _stop_streaming(self) -> None:
        """Arrête le streaming en cours."""
        if self.supervisor:
            self.supervisor.request_stop()
            self.supervisor = None
        self._on_state_change(State.STOPPED)

    def _on_device_selected(self, _event: Any = None) -> None:
        """L'utilisateur a choisi une enceinte dans la liste déroulante."""
        selected = self.device_combo.get()
        if not selected or selected == self.target_name:
            return

        self._select_device(selected)

    def _select_device(self, dev_name: str) -> None:
        """Met à jour l'enceinte cible et relance le flux si actif."""
        _LOGGER.info("Sélection de la nouvelle enceinte : %s", dev_name)
        self.target_name = dev_name
        self.device_combo.set(dev_name)

        self.config.device = DeviceConfig(name=dev_name)
        save_config(self.config, self.config_path)

        if self.current_state in (State.STREAMING, State.CONNECTING, State.DISCOVERING):
            self._start_streaming()
        elif self.tray_icon:
            self.tray_icon.update_menu()

    def _on_refresh_devices(self) -> None:
        """Lance une découverte manuelle des enceintes sur le réseau."""
        if self._is_scanning or not self.loop:
            return

        self._is_scanning = True
        self.btn_refresh.config(text="Recherche...", state="disabled")

        async def _scan() -> None:
            import pyatv

            found: list[str] = []
            try:
                res = await pyatv.scan(asyncio.get_running_loop(), timeout=3)
                found = [r.name for r in res if r.name]
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Erreur lors du scan manuel : %s", exc)

            def _update() -> None:
                self._is_scanning = False
                self.btn_refresh.config(text="🔄 Actualiser", state="normal")
                if found:
                    for name in found:
                        if name not in self.discovered_devices:
                            self.discovered_devices.append(name)
                    self.device_combo["values"] = sorted(set(self.discovered_devices))
                    if self.target_name not in self.discovered_devices and found:
                        self.target_name = found[0]
                        self.device_combo.set(self.target_name)
                if self.tray_icon:
                    self.tray_icon.update_menu()

            self.root.after(0, _update)

        asyncio.run_coroutine_threadsafe(_scan(), self.loop)

    def _on_volume_slider_changed(self, value: str) -> None:
        """Gère le déplacement du curseur de volume."""
        pct = int(float(value))
        self.lbl_volume_val.config(text=f"{pct} %")

        # Débouncing pour éviter de submerger la liaison réseau
        if self._volume_update_job:
            self.root.after_cancel(self._volume_update_job)

        self._volume_update_job = self.root.after(150, lambda: self._apply_volume(pct / 100.0))

    def _set_volume_preset(self, pct: int) -> None:
        """Applique une valeur de volume prédéfinie."""
        self.vol_scale.set(pct)
        self.lbl_volume_val.config(text=f"{pct} %")
        self._apply_volume(pct / 100.0)

    def _apply_volume(self, level: float) -> None:
        """Transmet la consigne de volume au superviseur et sauvegarde la config."""
        self.current_volume = level
        self.config.volume.default = level
        save_config(self.config, self.config_path)

        if self.supervisor and self.loop:
            asyncio.run_coroutine_threadsafe(
                self.supervisor.set_volume(level),
                self.loop,
            )
        if self.tray_icon:
            self.tray_icon.update_menu()

    def _open_logs_folder(self) -> None:
        """Ouvre le répertoire des fichiers logs dans l'Explorateur Windows."""
        log_dir = get_default_log_dir()
        if sys.platform == "win32":
            os.startfile(str(log_dir))  # type: ignore[attr-defined]

    def _copy_diagnostic(self) -> None:
        """Génère le diagnostic et le copie dans le presse-papier."""
        if not self.loop:
            return

        self.btn_diag.config(text="Génération...", state="disabled")

        async def _gen() -> None:
            report = await generate_diagnostic_report(self.config_path)

            def _apply() -> None:
                self.root.clipboard_clear()
                self.root.clipboard_append(report)
                self.btn_diag.config(text="✓ Copié !", state="normal")
                self.root.after(2500, lambda: self.btn_diag.config(text="📋 Copier le diagnostic"))

            self.root.after(0, _apply)

        asyncio.run_coroutine_threadsafe(_gen(), self.loop)

    async def _periodic_discovery(self) -> None:
        """Sonde le réseau local en arrière-plan."""
        import pyatv

        while True:
            try:
                loop = asyncio.get_running_loop()
                results = await pyatv.scan(loop, timeout=3)
                names = [r.name for r in results if r.name]
                if names:

                    def _sync_names(n_list: list[str]) -> None:
                        updated = False
                        for n in n_list:
                            if n not in self.discovered_devices:
                                self.discovered_devices.append(n)
                                updated = True
                        if updated:
                            self.device_combo["values"] = sorted(set(self.discovered_devices))
                            if self.tray_icon:
                                self.tray_icon.update_menu()

                    self.root.after(0, lambda: _sync_names(names))
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Découverte arrière-plan : %s", exc)

            await asyncio.sleep(60.0)

    def _try_restore_existing_window(self) -> None:
        """Si une instance tourne déjà, force sa fenêtre à s'afficher au premier plan."""
        if sys.platform != "win32":
            return
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.FindWindowW(None, "AirPlay Sender")
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)

    def quit_app(self) -> None:
        """Arrête complètement l'application et ferme la fenêtre."""
        _LOGGER.info("Fermeture complète de l'application.")
        self._stop_streaming()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()

    def _shutdown(self) -> None:
        """Nettoie les threads et ressources."""
        self._stop_streaming()
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.loop_thread and self.loop_thread.is_alive():
            self.loop_thread.join(timeout=2.0)


def run_gui_app(
    config: AppConfig,
    config_path: Path | None = None,
    start_minimized: bool = False,
) -> int:
    """Point d'entrée pour exécuter l'application complète GUI + Tray."""
    app = AirPlayGUI(config, config_path=config_path, start_minimized=start_minimized)
    return app.start()
