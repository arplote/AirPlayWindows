#!/usr/bin/env python3
"""Script prototype de streaming direct temps réel Windows -> Devialet.

Phase 4 du projet AirPlayWindows.
Capture le son système via WASAPI Loopback, le convertit via le pipeline
et le diffuse en continu sur l'enceinte Devialet via AirPlay/RAOP.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading

import pyatv
from pyatv.const import Protocol

from airplay_sender.audio_pipeline import AudioPipeline
from airplay_sender.capture import LoopbackCapture
from airplay_sender.metrics import MetricsLogger, SessionMetrics
from airplay_sender.streamer import AirPlayStreamer

_LOGGER = logging.getLogger("live_stream")


def capture_feeder_worker(
    capture: LoopbackCapture,
    pipeline: AudioPipeline,
    stop_event: threading.Event,
) -> None:
    """Thread ouvrier qui lit les frames WASAPI et les injecte dans le pipeline."""
    while not stop_event.is_set():
        data = capture.read(timeout=0.02)
        if data:
            pipeline.push(data)


async def delayed_stop_task(streamer: AirPlayStreamer, delay_sec: float) -> None:
    """Arrête proprement le streamer après un délai spécifié."""
    await asyncio.sleep(delay_sec)
    _LOGGER.info(
        "Durée demandée de %.1f secondes atteinte. Arrêt propre du flux...",
        delay_sec,
    )
    await streamer.close()


async def run_live_stream(
    device_name: str,
    buffer_ms: int = 800,
    volume: float | None = None,
    timeout: int = 8,
    duration: float | None = None,
) -> int:
    loop = asyncio.get_running_loop()

    print("=" * 75)
    print(" STREAMING AUDIO DIRECT WINDOWS -> ENCEINTE AIRPLAY")
    print("=" * 75)

    # 1. Découverte de l'enceinte
    print(f"Recherche de l'appareil '{device_name}' (timeout: {timeout}s)...")
    devices = await pyatv.scan(loop, timeout=timeout)
    target = next((d for d in devices if device_name.lower() in d.name.lower()), None)
    if not target:
        print(f"[ERREUR] Aucun appareil contenant '{device_name}' n'a été trouvé.")
        return 1

    print(f"[OK] Appareil trouvé : {target.name} ({target.address})")

    # 2. Initialisation de la capture audio
    capture = LoopbackCapture()
    print(
        f"[AUDIO] Capture initialisée sur '{capture.device_name}' "
        f"({capture.samplerate} Hz, {capture.channels} canaux, {capture.dtype})"
    )

    # 3. Initialisation du pipeline audio (rééchantillonnage 48k -> 44.1k si nécessaire)
    pipeline = AudioPipeline(
        in_rate=capture.samplerate,
        in_channels=capture.channels,
        out_rate=44100,
        out_channels=2,
        buffer_ms=buffer_ms,
    )
    print(
        f"[PIPELINE] Sortie 44100 Hz stéréo, tampon cible {buffer_ms} ms "
        f"(max {int(pipeline.max_buffer_bytes / pipeline.out_bytes_per_ms)} ms)"
    )

    # 4. Connexion AirPlay Streamer
    streamer = AirPlayStreamer()
    await streamer.connect(target, protocol=Protocol.RAOP)
    print(f"[AIRPLAY] Connecté à {target.name} via RAOP.")

    if volume is not None:
        await streamer.set_volume(volume)
        print(f"[AIRPLAY] Volume réglé à {volume:.1f}%.")
    else:
        print(f"[AIRPLAY] Volume actuel : {streamer.volume:.1f}%.")

    # 5. Démarrage de la capture et du thread feeder
    stop_event = threading.Event()
    capture.start()
    feeder_thread = threading.Thread(
        target=capture_feeder_worker,
        args=(capture, pipeline, stop_event),
        daemon=True,
    )
    feeder_thread.start()

    # 6. Suivi des métriques
    session_metrics = SessionMetrics()

    def get_metrics() -> SessionMetrics:
        session_metrics.total_underruns = pipeline.stats.underruns
        session_metrics.total_overflows = pipeline.stats.overflows
        session_metrics.total_silence_bytes = pipeline.stats.silence_bytes_generated
        session_metrics.record_buffer_level(pipeline.level_ms)
        return session_metrics

    metrics_logger = MetricsLogger(get_metrics, interval_seconds=30.0)
    metrics_logger.start()

    print("\n>>> STREAMING EN DIRECT EN COURS ! (Appuyez sur Ctrl+C pour arrêter)")
    print(">>> Lancez de la musique sur votre PC (YouTube, Spotify, etc.) pour écouter.\n")

    timer_task: asyncio.Task[None] | None = None
    if duration is not None:
        print(f"[INFO] Arrêt automatique configuré après {duration:.1f} secondes.")
        timer_task = asyncio.create_task(delayed_stop_task(streamer, duration))

    try:
        await streamer.stream(pipeline)
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("\nArrêt demandé...")
    finally:
        if timer_task and not timer_task.done():
            timer_task.cancel()
        stop_event.set()
        feeder_thread.join(timeout=1.0)
        capture.stop()
        metrics_logger.stop()
        await streamer.close()

        final = get_metrics()
        print("\n" + "=" * 75)
        print(" BILAN DE LA SESSION DE STREAMING")
        print("=" * 75)
        print(final.format_summary())
        print(
            f"Total octets capturés : {pipeline.stats.total_bytes_in} | "
            f"Total octets diffusés : {pipeline.stats.total_bytes_out}"
        )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diffuser en direct le son système Windows vers une enceinte AirPlay."
    )
    parser.add_argument(
        "--name",
        type=str,
        default="Phantom",
        help="Nom ou sous-chaîne du récepteur (défaut: Phantom)",
    )
    parser.add_argument(
        "--buffer-ms",
        type=int,
        default=800,
        help="Taille du tampon audio en millisecondes (défaut: 800)",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=None,
        help="Régler le volume en %% au démarrage (optionnel)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=8,
        help="Timeout de découverte en secondes (défaut: 8)",
    )
    parser.add_argument(
        "--duration",
        "-d",
        type=float,
        default=None,
        help="Durée maximale du stream en secondes (défaut: infini)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Activer les logs verbeux de debug",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    return asyncio.run(
        run_live_stream(
            device_name=args.name,
            buffer_ms=args.buffer_ms,
            volume=args.volume,
            timeout=args.timeout,
            duration=args.duration,
        )
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
