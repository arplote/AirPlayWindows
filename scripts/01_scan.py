#!/usr/bin/env python3
"""Script de scan réseau mDNS / AirPlay / RAOP.

Phase 1 du projet AirPlayWindows.
Permet d'identifier les récepteurs AirPlay sur le réseau local,
d'extraire leurs services et d'analyser leurs enregistrements TXT.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
from typing import Any

import pyatv
from pyatv.const import Protocol
from pyatv.interface import BaseConfig
from zeroconf import InterfaceChoice, IPVersion, ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

# Bitmask des fonctionnalités AirPlay connues
AIRPLAY_FEATURES: dict[int, str] = {
    0: "Video",
    1: "Photo",
    2: "VideoScreen",
    3: "Music / Audio",
    4: "RedundantStereo",
    5: "FairPlay",
    7: "MFi",
    9: "MetaData",
    11: "AudioStream",
    13: "AudioPCM",
    14: "ALAC",
    15: "AAC_LC",
    16: "AAC_ELD",
    17: "MultiChannel",
    18: "RSA_AUTH",
    19: "Pairing",
    20: "BufferedAudio",
    21: "PTP / NTP Timing",
    22: "ScreenMultiCodec",
    23: "SystemPairing",
    24: "PeerToPeer",
    25: "UnifiedMediaControl",
    26: "CoreUtilsPairing",
    27: "AirPlay2",
    29: "DynamicGrouping",
    30: "MFiHAP",
    32: "HKPairing",
    38: "TransientPairing",
    40: "MultiRoom",
    46: "AudioUncompressed",
    48: "AudioClock",
    51: "AudioMetadata",
}

CODECS_MAP: dict[str, str] = {
    "0": "PCM (16/44.1)",
    "1": "ALAC",
    "2": "AAC",
    "3": "AAC-ELD",
}

ENCRYPTION_MAP: dict[str, str] = {
    "0": "None (Chiffrement non requis)",
    "1": "RSA / AirPlay 1",
    "2": "FairPlay",
    "3": "MFi",
    "4": "FairPlay v2",
    "5": "ChaCha20-Poly1305 (AirPlay 2)",
}


def decode_features_bitmask(features_str: str) -> list[str]:
    """Décode la chaîne de features (ex: '0x445F8A00,0x1C340' ou '0x1234')."""
    decoded: list[str] = []
    if not features_str:
        return decoded

    parts = features_str.split(",")
    try:
        if len(parts) == 2:
            # Format lower,upper en hexadécimal
            val_lo = int(parts[0], 0)
            val_hi = int(parts[1], 0)
            full_mask = (val_hi << 32) | val_lo
        else:
            full_mask = int(parts[0], 0)

        for bit, label in sorted(AIRPLAY_FEATURES.items()):
            if (full_mask & (1 << bit)) != 0:
                decoded.append(f"bit {bit} ({label})")
    except ValueError:
        decoded.append(f"Valeur brute non analysable: {features_str}")

    return decoded


def decode_codecs(codecs_str: str) -> list[str]:
    if not codecs_str:
        return []
    return [CODECS_MAP.get(c.strip(), f"Inconnu ({c.strip()})") for c in codecs_str.split(",")]


def decode_encryption(et_str: str) -> list[str]:
    if not et_str:
        return []
    return [ENCRYPTION_MAP.get(e.strip(), f"Inconnu ({e.strip()})") for e in et_str.split(",")]


async def scan_zeroconf(
    interfaces: list[str] | None, timeout: float = 4.0
) -> dict[str, list[dict[str, Any]]]:
    """Scan secondaire indépendant avec zeroconf sur les types AirPlay et RAOP."""
    results: dict[str, list[dict[str, Any]]] = {
        "_raop._tcp.local.": [],
        "_airplay._tcp.local.": [],
    }

    zc_interfaces = interfaces if interfaces is not None else InterfaceChoice.All
    zc = AsyncZeroconf(interfaces=zc_interfaces, ip_version=IPVersion.V4Only)
    discovered_names: set[tuple[str, str]] = set()

    def on_service_state_change(
        zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        if state_change == ServiceStateChange.Added:
            discovered_names.add((service_type, name))

    browser = AsyncServiceBrowser(
        zc.zeroconf,
        ["_raop._tcp.local.", "_airplay._tcp.local."],
        handlers=[on_service_state_change],
    )

    try:
        await asyncio.sleep(timeout)
        for service_type, name in discovered_names:
            info = AsyncServiceInfo(service_type, name)
            if await info.async_request(zc.zeroconf, 3000):
                addresses = [socket.inet_ntoa(addr) for addr in info.addresses if len(addr) == 4]
                props = {
                    (k.decode("utf-8", "replace") if isinstance(k, bytes) else str(k)): (
                        v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
                    )
                    for k, v in info.properties.items()
                }
                results[service_type].append(
                    {
                        "name": name,
                        "server": info.server,
                        "addresses": addresses,
                        "port": info.port,
                        "properties": props,
                    }
                )
    finally:
        await browser.async_cancel()
        await zc.async_close()

    return results


async def run_scan(
    timeout: int = 10,
    name_filter: str | None = None,
    interface: str | None = None,
) -> tuple[list[BaseConfig], dict[str, Any]]:
    """Exécute les scans pyatv et zeroconf."""
    loop = asyncio.get_running_loop()
    interfaces = [interface] if interface else None

    # Création instance Zeroconf pour pyatv si interface spécifiée
    aiozc = (
        AsyncZeroconf(interfaces=interfaces, ip_version=IPVersion.V4Only) if interfaces else None
    )

    try:
        pyatv_scan_task = pyatv.scan(loop, timeout=timeout, aiozc=aiozc)
        zc_scan_task = scan_zeroconf(interfaces, timeout=min(timeout, 5))

        pyatv_results, zc_results = await asyncio.gather(
            pyatv_scan_task,
            zc_scan_task,
            return_exceptions=False,
        )
    finally:
        if aiozc:
            await aiozc.async_close()

    if name_filter:
        pyatv_results = [cfg for cfg in pyatv_results if name_filter.lower() in cfg.name.lower()]

    return pyatv_results, zc_results


def format_device_dict(cfg: BaseConfig) -> dict[str, Any]:
    """Formate la configuration pyatv en dictionnaire structuré."""
    info = cfg.device_info
    services_data: list[dict[str, Any]] = []

    for s in cfg.services:
        props = dict(s.properties)
        features_raw = props.get("ft") or props.get("features", "")
        codecs_raw = props.get("cn", "")
        et_raw = props.get("et", "")

        services_data.append(
            {
                "protocol": s.protocol.name
                if isinstance(s.protocol, Protocol)
                else str(s.protocol),
                "port": s.port,
                "pairing_requirement": str(s.pairing),
                "requires_password": s.requires_password,
                "properties_raw": props,
                "decoded": {
                    "features": decode_features_bitmask(features_raw),
                    "codecs": decode_codecs(codecs_raw),
                    "encryption": decode_encryption(et_raw),
                    "model": props.get("model") or props.get("am", ""),
                    "firmware": props.get("fv", ""),
                    "public_key": props.get("pk", ""),
                    "device_id": props.get("deviceid") or props.get("pi", ""),
                },
            }
        )

    return {
        "name": cfg.name,
        "address": str(cfg.address) if cfg.address else None,
        "identifier": cfg.identifier,
        "all_identifiers": list(cfg.all_identifiers),
        "device_info": {
            "model": str(info.model) if info else None,
            "model_str": info.model_str if info else None,
            "operating_system": str(info.operating_system) if info else None,
            "version": info.version if info else None,
            "mac": info.mac if info else None,
            "raw_model": info.raw_model if info else None,
        },
        "services": services_data,
    }


def print_human_report(devices: list[dict[str, Any]], zc_results: dict[str, Any]) -> None:
    """Affiche un rapport clair et détaillé en console."""
    sep = "=" * 80
    subsep = "-" * 80

    print(sep)
    print(f" SCAN DU RÉSEAU LOCAL — {len(devices)} APPAREIL(S) DÉTECTÉ(S) VIA PYATV")
    print(sep)

    if not devices:
        print("\n [!] Aucun récepteur AirPlay trouvé via pyatv.")
        print("     Vérifiez que votre PC et l'enceinte sont sur le même sous-réseau (ex: Wi-Fi),")
        print("     et que le profil réseau Windows est configuré en 'Privé'.\n")

    for i, dev in enumerate(devices, start=1):
        print(f"\n[Appareil {i}] : {dev['name']}")
        print(f"  Adresse IP     : {dev['address']}")
        print(f"  Identifiant    : {dev['identifier']}")
        print(f"  Adresse MAC    : {dev['device_info']['mac']}")
        print(
            f"  Modèle         : {dev['device_info']['model_str'] or dev['device_info']['model']}"
        )
        os_info = dev["device_info"]["operating_system"]
        ver_info = dev["device_info"]["version"]
        print(f"  OS / Firmware  : {os_info} (version: {ver_info})")
        print(f"  Tous les IDs   : {', '.join(dev['all_identifiers'])}")

        print("\n  Services disponibles :")
        for s in dev["services"]:
            proto = s["protocol"]
            port = s["port"]
            pairing = s["pairing_requirement"]
            pw = "Oui" if s["requires_password"] else "Non"
            print(f"    * Protocole : {proto} (port {port})")
            print(f"        - Appairage requis : {pairing}")
            print(f"        - Mot de passe     : {pw}")

            dec = s["decoded"]
            if dec["codecs"]:
                print(f"        - Codecs supportés : {', '.join(dec['codecs'])}")
            if dec["encryption"]:
                print(f"        - Chiffrements     : {', '.join(dec['encryption'])}")
            if dec["firmware"]:
                print(f"        - Firmware audio   : {dec['firmware']}")
            if dec["public_key"]:
                print(f"        - Clé publique pk  : {dec['public_key'][:16]}... (présente)")
            if dec["features"]:
                print(f"        - Fonctionnalités  : {', '.join(dec['features'][:8])}")
                if len(dec["features"]) > 8:
                    print(f"                             ... et {len(dec['features']) - 8} autres")

            raw_txt = s["properties_raw"]
            print(f"        - TXT records bruts: {raw_txt}")

        print(subsep)

    print("\n" + sep)
    print(" SCAN SECONDAIRE INDÉPENDANT (ZEROCONF DIRECT)")
    print(sep)
    for stype, items in zc_results.items():
        print(f" Service {stype} ({len(items)} entrées) :")
        for item in items:
            print(f"   - {item['name']} -> {item['addresses']}:{item['port']}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Scan AirPlay & RAOP receivers on local network.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Durée du scan en secondes (défaut: 10)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Filtrer par nom d'appareil (ex: Phantom)",
    )
    parser.add_argument(
        "--interface",
        type=str,
        default=None,
        help="Adresse IP de l'interface réseau locale à écouter (ex: 192.168.1.50)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Afficher la sortie brute au format JSON",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Activer les logs détaillés de découverte",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    configs, zc_results = await run_scan(
        timeout=args.timeout,
        name_filter=args.name,
        interface=args.interface,
    )

    devices_data = [format_device_dict(c) for c in configs]

    if args.json:
        payload = {
            "pyatv_devices": devices_data,
            "zeroconf_services": zc_results,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_human_report(devices_data, zc_results)

    return 0 if devices_data else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
