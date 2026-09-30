#!/usr/bin/env python3
"""Script de test de capture audio WASAPI Loopback vers fichier WAV.

Phase 3 du projet AirPlayWindows.
Capture le son joué sous Windows sur la sortie par défaut et le sauvegarde en WAV.
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

import pyaudiowpatch as pyaudio

from airplay_sender.capture import LoopbackCapture


def list_loopback_devices() -> None:
    """Affiche la liste de tous les périphériques WASAPI loopback disponibles."""
    pa = pyaudio.PyAudio()
    try:
        print("Périphériques WASAPI Loopback disponibles :")
        print("-" * 75)
        for dev in pa.get_loopback_device_info_generator():
            idx = dev["index"]
            name = dev["name"]
            sr = int(dev["defaultSampleRate"])
            ch = dev["maxInputChannels"]
            print(f"[{idx:2d}] {name} (Taux: {sr} Hz, Canaux: {ch})")
        print("-" * 75)
        default_lb = pa.get_default_wasapi_loopback()
        if default_lb:
            print(f"Périphérique par défaut : [{default_lb['index']}] {default_lb['name']}\n")
    finally:
        pa.terminate()


def capture_to_wav(
    duration: float,
    output_path: Path,
    device_index: int | None = None,
) -> bool:
    """Capture l'audio système pendant N secondes et enregistre dans un fichier WAV."""
    print("=" * 75)
    print(" CAPTURE DU SON SYSTÈME WINDOWS (WASAPI LOOPBACK)")
    print("=" * 75)

    capture = LoopbackCapture(device_index=device_index)
    print(f"Périphérique sélectionné : {capture.device_name}")
    print(
        f"Format natif             : {capture.samplerate} Hz, "
        f"{capture.channels} canaux, {capture.dtype}"
    )
    print(f"Durée demandée           : {duration:.1f} secondes")
    print(f"Fichier de sortie        : {output_path.resolve()}")
    print("-" * 75)
    print(">>> ASTUCE : Si rien ne joue sous Windows, WASAPI ne produit aucun paquet audio.")
    print(">>> Lancez un son / une vidéo pour vérifier la capture réelle.")
    print("-" * 75)

    frames: list[bytes] = []
    capture.start()
    t_start = time.perf_counter()

    try:
        while time.perf_counter() - t_start < duration:
            chunk = capture.read(timeout=0.05)
            if chunk:
                frames.append(chunk)
    finally:
        capture.stop()

    elapsed = time.perf_counter() - t_start
    total_bytes = sum(len(f) for f in frames)

    print(f"\nCapture terminée en {elapsed:.2f} secondes.")
    print(f"Blocs reçus : {len(frames)} ({total_bytes} octets).")
    print(f"Débordements de file (overflows) : {capture.overflow_count}")

    if not frames:
        print("\n[ATTENTION] Aucun son n'a été capturé (0 octet).")
        print(
            "C'est le comportement normal de WASAPI loopback "
            "quand aucun flux audio n'est actif sur Windows."
        )
        return False

    # Écriture dans le fichier WAV
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(capture.channels)
        wf.setsampwidth(2)  # int16 = 2 octets
        wf.setframerate(capture.samplerate)
        wf.writeframes(b"".join(frames))

    print(f"\n[SUCCÈS] Fichier audio WAV enregistré : {output_path.resolve()}")
    print(f"Taille du fichier : {output_path.stat().st_size / 1024:.1f} Ko")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capturer le son système Windows vers un fichier WAV."
    )
    parser.add_argument(
        "--duration",
        "-d",
        type=float,
        default=5.0,
        help="Durée de capture en secondes (défaut: 5.0)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("recorded_test.wav"),
        help="Nom du fichier WAV de sortie (défaut: recorded_test.wav)",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Index du périphérique loopback spécifique (optionnel)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Lister les périphériques audio WASAPI loopback et quitter",
    )

    args = parser.parse_args()

    if args.list_devices:
        list_loopback_devices()
        return 0

    success = capture_to_wav(
        duration=args.duration,
        output_path=args.output,
        device_index=args.device,
    )

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
