"""Génération du rapport de diagnostic pour le support et le dépannage.

Phase 6 du projet AirPlayWindows.
Rassemble les versions de l'environnement, les interfaces réseau,
les périphériques WASAPI loopback, les enceintes découvertes et les derniers logs.
"""

from __future__ import annotations

import asyncio
import platform
import socket
import sys
from pathlib import Path
from typing import Any

import pyatv
import pyaudiowpatch as pyaudio
from pyatv.const import Protocol

from airplay_sender import __version__
from airplay_sender.config import AppConfig, load_config
from airplay_sender.logging_setup import get_default_log_dir


async def gather_discovered_devices(timeout: int = 3) -> list[dict[str, Any]]:
    """Effectue un scan rapide pyatv pour le diagnostic."""
    devices: list[dict[str, Any]] = []
    try:
        loop = asyncio.get_running_loop()
        results = await pyatv.scan(loop, timeout=timeout)
        for conf in results:
            devices.append(
                {
                    "name": conf.name,
                    "address": str(conf.address),
                    "identifier": conf.identifier,
                    "services": [
                        {
                            "protocol": s.protocol.name
                            if isinstance(s.protocol, Protocol)
                            else str(s.protocol),
                            "port": s.port,
                        }
                        for s in conf.services
                    ],
                }
            )
    except Exception as exc:  # noqa: BLE001
        devices.append({"error": f"Erreur de scan : {exc}"})
    return devices


def gather_audio_devices() -> dict[str, Any]:
    """Collecte les informations sur les périphériques audio WASAPI loopback."""
    info: dict[str, Any] = {"loopback_devices": [], "default_loopback": None}
    pa = pyaudio.PyAudio()
    try:
        for dev in pa.get_loopback_device_info_generator():
            info["loopback_devices"].append(
                {
                    "index": dev.get("index"),
                    "name": dev.get("name"),
                    "sample_rate": dev.get("defaultSampleRate"),
                    "channels": dev.get("maxInputChannels"),
                }
            )
        default_lb = pa.get_default_wasapi_loopback()
        if default_lb:
            info["default_loopback"] = {
                "index": default_lb.get("index"),
                "name": default_lb.get("name"),
                "sample_rate": default_lb.get("defaultSampleRate"),
                "channels": default_lb.get("maxInputChannels"),
            }
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
    finally:
        pa.terminate()
    return info


def gather_network_interfaces() -> list[dict[str, Any]]:
    """Collecte la liste des adresses IP locales."""
    interfaces: list[dict[str, Any]] = []
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            interfaces.append({"hostname": hostname, "ip": ip})
    except Exception as exc:  # noqa: BLE001
        interfaces.append({"error": str(exc)})
    return interfaces


def read_recent_logs(max_lines: int = 50) -> list[str]:
    """Lit les N dernières lignes du fichier de logs applicatif."""
    log_file = get_default_log_dir() / "app.log"
    if not log_file.exists():
        return ["(Aucun fichier de log trouvé)"]
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-max_lines:]
    except Exception as exc:  # noqa: BLE001
        return [f"(Erreur de lecture des logs : {exc})"]


async def generate_diagnostic_report(config_path: Path | None = None) -> str:
    """Génère un rapport texte complet au format Markdown pour le support."""
    config: AppConfig = load_config(config_path)

    # Récupération asynchrone des appareils
    discovered = await gather_discovered_devices(timeout=3)
    audio_info = gather_audio_devices()
    net_info = gather_network_interfaces()
    logs = read_recent_logs(50)

    # Masquage des credentials dans le dump config
    masked_config = config.model_dump()
    if "credentials" in masked_config:
        for k in masked_config["credentials"]:
            if masked_config["credentials"][k]:
                masked_config["credentials"][k] = "***"

    lines = [
        "=" * 70,
        f" RAPPORT DE DIAGNOSTIC - AirPlay Sender v{__version__}",
        "=" * 70,
        "",
        "## Système & Environnement",
        f"- OS             : {platform.platform()} ({platform.architecture()[0]})",
        f"- Python         : {sys.version.split()[0]} ({sys.executable})",
        f"- Machine        : {platform.node()} ({platform.machine()})",
        "",
        "## Interfaces Réseau",
    ]

    for iface in net_info:
        if "error" in iface:
            lines.append(f"  - Erreur : {iface['error']}")
        else:
            lines.append(f"  - {iface.get('hostname')}: {iface.get('ip')}")

    lines.extend(
        [
            "",
            "## Périphériques Audio WASAPI (Loopback)",
        ]
    )
    def_lb = audio_info.get("default_loopback")
    if def_lb:
        lines.append(
            f"  * Sortie par défaut : [{def_lb['index']}] {def_lb['name']} "
            f"({int(def_lb['sample_rate'])} Hz, {def_lb['channels']} ch)"
        )
    else:
        lines.append("  * Sortie par défaut : AUCUN DÉTECTÉ")

    lines.append("  * Tous les loopbacks disponibles :")
    for dev in audio_info.get("loopback_devices", []):
        lines.append(
            f"    - [{dev['index']}] {dev['name']} "
            f"({int(dev['sample_rate'])} Hz, {dev['channels']} ch)"
        )

    lines.extend(
        [
            "",
            "## Appareils AirPlay Découverts (Scan mDNS 3s)",
        ]
    )
    if discovered:
        for dev in discovered:
            if "error" in dev:
                lines.append(f"  - Erreur : {dev['error']}")
            else:
                lines.append(
                    f"  - {dev['name']} ({dev['address']}) - ID: {dev['identifier']} "
                    f"Services: {dev['services']}"
                )
    else:
        lines.append("  (Aucun appareil découvert)")

    lines.extend(
        [
            "",
            "## Configuration Active (Credentials masqués)",
        ]
    )
    for section, vals in masked_config.items():
        lines.append(f"  [{section}] {vals}")

    lines.extend(
        [
            "",
            "## Dernières Traces de Log",
        ]
    )
    for line in logs:
        lines.append(f"  {line}")

    lines.extend(["", "=" * 70])
    return "\n".join(lines)
