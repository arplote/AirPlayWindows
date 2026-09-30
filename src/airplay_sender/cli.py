"""Commandes de l'interface en ligne de commande (CLI) d'AirPlay Sender.

Phase 6 du projet AirPlayWindows.
Commandes disponibles :
- scan : recherche des récepteurs sur le réseau local
- pair : appairage interactif
- run  : streaming console sans interface graphique
- diag : rapport complet d'analyse pour le diagnostic
- tray : lancement du mode icône système (System Tray)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pyatv
from pyatv.const import PairingRequirement, Protocol
from pyatv.interface import BaseConfig

from airplay_sender.config import (
    AppConfig,
    DeviceConfig,
    save_config,
)
from airplay_sender.diagnostics import generate_diagnostic_report
from airplay_sender.supervisor import State, Supervisor

_LOGGER = logging.getLogger("airplay_sender.cli")


async def cmd_scan(args: argparse.Namespace, config: AppConfig) -> int:
    """Scanne le réseau local pour détecter les récepteurs AirPlay/RAOP."""
    print("=" * 70)
    print(f" SCAN DU RÉSEAU LOCAL (timeout={args.timeout}s)...")
    print("=" * 70)

    loop = asyncio.get_running_loop()
    try:
        results = await pyatv.scan(loop, timeout=args.timeout)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERREUR] Échec du scan réseau : {exc}", file=sys.stderr)
        return 1

    if not results:
        print("[INFO] Aucun récepteur AirPlay ou RAOP détecté sur le réseau.")
        print("Vérifiez que le PC et l'enceinte sont sur le même réseau Wi-Fi / Ethernet,")
        print("et que le profil réseau Windows est bien défini sur 'Privé'.")
        return 0

    devices_list: list[dict[str, Any]] = []
    for idx, cfg in enumerate(results, start=1):
        services_desc = []
        for s in cfg.services:
            p_name = s.protocol.name if isinstance(s.protocol, Protocol) else str(s.protocol)
            services_desc.append(f"{p_name}:{s.port} (Pairing: {s.pairing.name})")

        dev_data = {
            "index": idx,
            "name": cfg.name,
            "address": str(cfg.address),
            "identifier": cfg.identifier,
            "model": cfg.device_info.model_str or cfg.device_info.model or "Inconnu",
            "services": services_desc,
        }
        devices_list.append(dev_data)

    if args.json:
        print(json.dumps(devices_list, indent=2, ensure_ascii=False))
        return 0

    print(f"\n{len(results)} appareil(s) trouvé(s) :\n")
    for d in devices_list:
        print(f"[{d['index']}] {d['name']}")
        print(f"    Adresse IP  : {d['address']}")
        print(f"    Identifiant : {d['identifier']}")
        print(f"    Modèle      : {d['model']}")
        print(f"    Services    : {', '.join(d['services'])}\n")

    if args.save:
        # Sauvegarde d'un appareil par nom ou index
        selected_cfg: BaseConfig | None = None
        for cfg in results:
            if args.save.lower() in cfg.name.lower() or args.save == cfg.identifier:
                selected_cfg = cfg
                break
        if not selected_cfg and args.save.isdigit():
            i = int(args.save) - 1
            if 0 <= i < len(results):
                selected_cfg = results[i]

        if selected_cfg:
            config.device = DeviceConfig(
                name=selected_cfg.name,
                identifier=selected_cfg.identifier or "",
                last_known_ip=str(selected_cfg.address),
            )
            save_config(config, args.config)
            print(f"[SUCCÈS] Appareil par défaut enregistré : {selected_cfg.name}")
        else:
            print(f"[ATTENTION] Impossible de trouver l'appareil '{args.save}' pour la sauvegarde.")

    return 0


async def cmd_pair(args: argparse.Namespace, config: AppConfig) -> int:
    """Appaire interactivement le PC avec un récepteur AirPlay/RAOP."""
    target = args.device or config.device.name or config.device.identifier
    if not target:
        print(
            "[ERREUR] Aucun appareil spécifié. "
            "Utilisez --device <nom> ou lancez d'abord 'scan --save'.",
            file=sys.stderr,
        )
        return 1

    print(f"Recherche de l'appareil '{target}' pour l'appairage...")
    loop = asyncio.get_running_loop()
    results = await pyatv.scan(loop, timeout=5)

    matching_dev: BaseConfig | None = None
    for d in results:
        if target.lower() in d.name.lower() or target == d.identifier:
            matching_dev = d
            break

    if not matching_dev:
        print(f"[ERREUR] Appareil '{target}' introuvable sur le réseau.", file=sys.stderr)
        return 1

    print(f"Appareil trouvé : {matching_dev.name} ({matching_dev.address})")

    # Vérification des protocoles disponibles
    raop_service = matching_dev.get_service(Protocol.RAOP)
    airplay_service = matching_dev.get_service(Protocol.AirPlay)

    if raop_service and raop_service.pairing == PairingRequirement.NotNeeded:
        print(
            "\n[INFO] Cet appareil supporte le protocole RAOP "
            "sans nécessiter d'appairage par code PIN."
        )
        config.device = DeviceConfig(
            name=matching_dev.name,
            identifier=matching_dev.identifier or "",
            last_known_ip=str(matching_dev.address),
        )
        save_config(config, args.config)
        print(f"[SUCCÈS] Configuration mise à jour pour '{matching_dev.name}'.")
        return 0

    protocol_to_pair = Protocol.AirPlay if airplay_service else Protocol.RAOP
    print(f"Démarrage de la procédure d'appairage ({protocol_to_pair.name})...")

    try:
        pairing_handler = await pyatv.pair(matching_dev, protocol=protocol_to_pair, loop=loop)
        await pairing_handler.begin()

        if pairing_handler.device_provides_pin:
            pin = input("Entrez le code PIN affiché sur l'écran / enceinte : ").strip()
            pairing_handler.pin(pin)

        await pairing_handler.finish()

        credentials = pairing_handler.service.credentials
        if protocol_to_pair == Protocol.RAOP:
            config.credentials.raop = credentials or ""
        else:
            config.credentials.airplay = credentials or ""

        config.device = DeviceConfig(
            name=matching_dev.name,
            identifier=matching_dev.identifier or "",
            last_known_ip=str(matching_dev.address),
        )
        save_config(config, args.config)
        print(f"[SUCCÈS] Appairage réussi et enregistré pour '{matching_dev.name}' !")
        return 0

    except Exception as exc:  # noqa: BLE001
        print(f"[ERREUR] Échec de l'appairage : {exc}", file=sys.stderr)
        return 1


async def cmd_run(args: argparse.Namespace, config: AppConfig) -> int:
    """Lance la diffusion continue du son Windows en console."""
    target_name = args.device or config.device.name
    if not target_name:
        print(
            "[ERREUR] Aucun appareil configuré. "
            "Lancez 'python -m airplay_sender scan --save <nom>'",
            file=sys.stderr,
        )
        return 1

    buffer_ms = args.buffer_ms or config.audio.buffer_ms
    volume = args.volume if args.volume is not None else config.volume.default
    credentials = config.credentials.raop or None

    print("=" * 70)
    print(" DÉMARRAGE DU STREAMING AIRPLAY WINDOWS")
    print(f" Cible             : {target_name}")
    print(f" Tampon audio      : {buffer_ms} ms")
    if volume is not None:
        print(f" Volume initial    : {volume * 100:.0f} %")
    print("=" * 70)
    print("Appuyez sur Ctrl+C pour arrêter le streaming.\n")

    def _state_listener(new_state: State) -> None:
        print(f"[*] État superviseur : {new_state.value}")

    supervisor = Supervisor(
        target_name=target_name,
        buffer_ms=buffer_ms,
        raop_latency_ms=getattr(config.audio, "raop_latency_ms", 250),
        volume=volume,
        credentials=credentials,
        on_state_change=_state_listener,
        prefer_virtual_cable=getattr(config.audio, "prefer_virtual_cable", True),
        capture_device=config.audio.capture_device,
    )

    try:
        await supervisor.run()
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        print("\nArrêt demandé...")
    finally:
        supervisor.request_stop()
        print("[*] Streaming arrêté proprement.")

    return 0


async def cmd_diag(args: argparse.Namespace, config: AppConfig) -> int:
    """Génère et affiche le rapport de diagnostic."""
    print("Génération du rapport de diagnostic en cours...")
    report = await generate_diagnostic_report(config_path=args.config)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(report, encoding="utf-8")
        print(f"[SUCCÈS] Rapport de diagnostic écrit dans {out_path.resolve()}")
    else:
        print(report)

    return 0


def cmd_tray(args: argparse.Namespace, config: AppConfig) -> int:
    """Lance l'application en mode barre d'état (System Tray)."""
    try:
        from airplay_sender.tray import run_tray_app

        return run_tray_app(config, config_path=args.config)
    except ImportError as exc:
        print(
            f"[ERREUR] Le composant System Tray n'est pas encore disponible : {exc}",
            file=sys.stderr,
        )
        return 1


def cmd_gui(args: argparse.Namespace, config: AppConfig) -> int:
    """Lance l'application en mode interface graphique (GUI) avec fenêtre et System Tray."""
    try:
        from airplay_sender.gui import run_gui_app

        start_min = getattr(args, "minimized", False)
        return run_gui_app(config, config_path=args.config, start_minimized=start_min)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[ERREUR] Impossible de lancer l'interface graphique : {exc}",
            file=sys.stderr,
        )
        return 1
