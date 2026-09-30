#!/usr/bin/env python3
"""Script d'envoi d'un fichier audio vers un récepteur AirPlay/RAOP.

Phase 2 du projet AirPlayWindows (Preuve de concept).
Permet de valider qu'un son sort de l'enceinte Devialet depuis Windows via pyatv.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import pyatv
from pyatv.const import PairingRequirement, Protocol
from pyatv.interface import AppleTV, BaseConfig

_LOGGER = logging.getLogger("send_file")


async def find_device(
    loop: asyncio.AbstractEventLoop, name: str, timeout: int = 8
) -> BaseConfig | None:
    """Recherche l'appareil AirPlay/RAOP correspondant au nom spécifié."""
    _LOGGER.info("Recherche des appareils AirPlay sur le réseau (timeout=%ds)...", timeout)
    devices = await pyatv.scan(loop, timeout=timeout)
    for dev in devices:
        if name.lower() in dev.name.lower():
            return dev
    return None


async def handle_pairing_if_needed(
    config: BaseConfig,
    protocol: Protocol,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Gère l'appairage si le service l'exige."""
    service = config.get_service(protocol)
    if not service:
        return

    if service.pairing == PairingRequirement.NotNeeded:
        _LOGGER.info("Appairage non requis pour %s (%s).", config.name, protocol.name)
        return

    _LOGGER.info(
        "Appairage requis pour %s (%s). Démarrage de la procédure...",
        config.name,
        protocol.name,
    )
    handler = await pyatv.pair(config, protocol, loop)
    try:
        await handler.begin()
        if handler.device_provides_pin:
            pin = input(f"Entrez le code PIN affiché pour {config.name} : ").strip()
            handler.pin(int(pin))
        await handler.finish()
        _LOGGER.info("Appairage réussi !")
    except Exception as exc:
        _LOGGER.error("Échec de l'appairage : %s", exc)
        await handler.close()
        raise


async def stream_audio_file(
    atv: AppleTV,
    file_path: Path,
    target_volume: float | None = None,
) -> dict[str, float | str]:
    """Diffuse le fichier audio et mesure les temps d'exécution."""
    metrics: dict[str, float | str] = {}

    # Lecture du volume initial
    initial_volume = atv.audio.volume
    metrics["initial_volume"] = initial_volume
    print(f"  Volume actuel de l'enceinte : {initial_volume:.1f}%")

    if target_volume is not None:
        print(f"  Réglage du volume à {target_volume:.1f}%...")
        await atv.audio.set_volume(target_volume)
        new_vol = atv.audio.volume
        metrics["set_volume"] = new_vol
        print(f"  Nouveau volume : {new_vol:.1f}%")

    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    print(f"\n  Démarrage de la diffusion du fichier : {file_path.name} ({file_size_mb:.2f} Mo)")
    print("  -> Écoutez attentivement l'enceinte...")

    t_start = time.perf_counter()
    try:
        await atv.stream.stream_file(str(file_path.resolve()))
        t_end = time.perf_counter()
        duration = t_end - t_start
        metrics["stream_duration_sec"] = duration
        print(f"  Diffusion terminée avec succès en {duration:.2f} secondes.")
    except Exception as exc:
        metrics["error"] = str(exc)
        print(f"  [ERREUR] Échec de la diffusion : {exc}")
        raise

    return metrics


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diffuser un fichier audio vers une enceinte AirPlay/RAOP."
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=Path("tests/assets/sine_440hz_5s.wav"),
        help="Chemin vers le fichier audio (WAV, MP3, FLAC)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="Phantom",
        help="Nom ou sous-chaîne du récepteur (défaut: Phantom)",
    )
    parser.add_argument(
        "--protocol",
        choices=["raop", "airplay"],
        default="raop",
        help="Protocole à utiliser (défaut: raop)",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=None,
        help="Niveau de volume souhaité en %% (0.0 à 100.0)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=8,
        help="Timeout de découverte en secondes (défaut: 8)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Activer les logs verbeux (DEBUG)",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.file.is_file():
        print(f"[ERREUR] Le fichier audio {args.file} n'existe pas.")
        return 1

    selected_proto = Protocol.RAOP if args.protocol == "raop" else Protocol.AirPlay

    loop = asyncio.get_running_loop()
    print("=" * 70)
    print(f" TEST DE STREAMING AIRPLAY/RAOP VERS '{args.name}'")
    print("=" * 70)

    # 1. Découverte
    t_scan_start = time.perf_counter()
    device = await find_device(loop, name=args.name, timeout=args.timeout)
    if not device:
        print(f"[ERREUR] Aucun récepteur contenant '{args.name}' n'a été trouvé.")
        return 1
    t_scan = time.perf_counter() - t_scan_start
    print(f"[OK] Appareil trouvé en {t_scan:.2f}s : {device.name} ({device.address})")

    # 2. Vérification / appairage
    await handle_pairing_if_needed(device, selected_proto, loop)

    # 3. Connexion
    print(f"\nConnexion à {device.name} via le protocole {selected_proto.name}...")
    t_conn_start = time.perf_counter()
    atv = await pyatv.connect(device, loop, protocol=selected_proto)
    t_conn = time.perf_counter() - t_conn_start
    print(f"[OK] Connecté en {t_conn:.2f}s.")

    try:
        # 4. Diffusion du fichier
        await stream_audio_file(atv, args.file, target_volume=args.volume)
    finally:
        print("\nFermeture de la session...")
        atv.close()
        print("Session fermée.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
