"""Pipeline audio : conversion, rééchantillonnage soxr, ring buffer et silence.

Phase 4 du projet AirPlayWindows.
Assure la transition fluide et sans clics entre la capture WASAPI (souvent 48 kHz)
et le diffuseur AirPlay/RAOP (44.1 kHz, 16 bits stéréo).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
import soxr

_LOGGER = logging.getLogger("airplay_sender.pipeline")


@dataclass
class PipelineStats:
    """Statistiques de fonctionnement du pipeline audio."""

    total_bytes_in: int = 0
    total_bytes_out: int = 0
    underruns: int = 0
    overflows: int = 0
    silence_bytes_generated: int = 0
    current_level_ms: float = 0.0
    average_level_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "bytes_in": self.total_bytes_in,
            "bytes_out": self.total_bytes_out,
            "underruns": self.underruns,
            "overflows": self.overflows,
            "silence_bytes": self.silence_bytes_generated,
            "buffer_level_ms": round(self.current_level_ms, 1),
            "avg_level_ms": round(self.average_level_ms, 1),
        }


class AudioPipeline:
    """Pipeline de traitement et de mise en mémoire tampon audio pour AirPlay."""

    def __init__(
        self,
        in_rate: int = 48000,
        in_channels: int = 2,
        out_rate: int = 44100,
        out_channels: int = 2,
        buffer_ms: int = 800,
    ) -> None:
        self.in_rate = in_rate
        self.in_channels = in_channels
        self.out_rate = out_rate
        self.out_channels = out_channels
        self.buffer_ms = buffer_ms

        self.bytes_per_sample = 2  # int16
        self.out_bytes_per_frame = self.out_channels * self.bytes_per_sample
        self.out_bytes_per_ms = (self.out_rate * self.out_bytes_per_frame) / 1000.0

        # Dimensionnement du ring buffer
        self.target_buffer_bytes = int(self.buffer_ms * self.out_bytes_per_ms)
        self.max_buffer_bytes = int(self.target_buffer_bytes * 2.0)

        self._lock = threading.Lock()
        self._buffer = bytearray()
        self.stats = PipelineStats()

        # Resampler soxr streaming si changement de fréquence
        self._resampler: soxr.ResampleStream | None = None
        if self.in_rate != self.out_rate:
            self._resampler = soxr.ResampleStream(
                in_rate=self.in_rate,
                out_rate=self.out_rate,
                num_channels=self.out_channels,
                dtype=np.int16,
                quality="MQ",
            )

        self._level_history: list[float] = []

    @property
    def level_ms(self) -> float:
        """Remplissage courant du tampon en millisecondes."""
        with self._lock:
            return len(self._buffer) / self.out_bytes_per_ms

    def push(self, raw: bytes) -> None:
        """Ajoute des données audio brutes natives (appelé depuis le thread de capture)."""
        if not raw:
            return

        self.stats.total_bytes_in += len(raw)

        # 1. Conversion en tableau NumPy int16
        samples = np.frombuffer(raw, dtype=np.int16)
        if len(samples) % self.in_channels != 0:
            samples = samples[: len(samples) - (len(samples) % self.in_channels)]

        frames_in = len(samples) // self.in_channels
        if frames_in == 0:
            return

        reshaped = samples.reshape((frames_in, self.in_channels))

        # 2. Downmix éventuel vers 2 canaux stéréo
        if self.in_channels == 1 and self.out_channels == 2:
            stereo = np.column_stack((reshaped[:, 0], reshaped[:, 0]))
        elif self.in_channels > 2:
            # Downmix 5.1/7.1 basique : G = L + 0.7*C + 0.7*Ls ; D = R + 0.7*C + 0.7*Rs
            left = reshaped[:, 0].astype(np.float32)
            right = reshaped[:, 1].astype(np.float32)
            center = reshaped[:, 2].astype(np.float32) if self.in_channels > 2 else 0.0
            downmixed_left = np.clip(left + 0.707 * center, -32768, 32767).astype(np.int16)
            downmixed_right = np.clip(right + 0.707 * center, -32768, 32767).astype(np.int16)
            stereo = np.column_stack((downmixed_left, downmixed_right))
        else:
            stereo = reshaped

        # 3. Rééchantillonnage vers out_rate (44.1 kHz)
        if self._resampler is not None:
            resampled = self._resampler.resample_chunk(stereo)
            processed_bytes = resampled.tobytes()
        else:
            processed_bytes = stereo.tobytes()

        if not processed_bytes:
            return

        # 4. Stockage dans le ring buffer thread-safe avec gestion d'overflow
        with self._lock:
            self._buffer.extend(processed_bytes)
            current_len = len(self._buffer)

            if current_len > self.max_buffer_bytes:
                # Dépassement : on jette les données les plus anciennes pour préserver la latence
                drop_bytes = current_len - self.target_buffer_bytes
                del self._buffer[:drop_bytes]
                self.stats.overflows += 1
                _LOGGER.debug(
                    "Buffer overflow : %d octets supprimés (buffer=%d ms)",
                    drop_bytes,
                    int(len(self._buffer) / self.out_bytes_per_ms),
                )

            lvl = len(self._buffer) / self.out_bytes_per_ms
            self.stats.current_level_ms = lvl
            self._record_level(lvl)

    def pull(self, nbytes: int) -> bytes:
        """Extrait nbytes du buffer.

        Renvoie TOUJOURS exactement nbytes.
        Si le buffer est insuffisant ou vide, produit du silence (zéros).
        """
        if nbytes <= 0:
            return b""

        with self._lock:
            available = len(self._buffer)
            if available >= nbytes:
                # Tampon suffisant : lecture normale
                chunk = bytes(self._buffer[:nbytes])
                del self._buffer[:nbytes]
            elif available > 0:
                # Tampon partiel : données restantes complétées par du silence
                chunk = bytes(self._buffer)
                del self._buffer[:]
                missing = nbytes - available
                chunk += b"\x00" * missing
                self.stats.underruns += 1
                self.stats.silence_bytes_generated += missing
            else:
                # Tampon vide (silence système) : génération continue de zéros
                chunk = b"\x00" * nbytes
                self.stats.underruns += 1
                self.stats.silence_bytes_generated += nbytes

            lvl = len(self._buffer) / self.out_bytes_per_ms
            self.stats.current_level_ms = lvl
            self.stats.total_bytes_out += len(chunk)
            self._record_level(lvl)

        return chunk

    def _record_level(self, level: float) -> None:
        self._level_history.append(level)
        if len(self._level_history) > 100:
            self._level_history.pop(0)
        self.stats.average_level_ms = sum(self._level_history) / len(self._level_history)

    def clear(self) -> None:
        """Vide le buffer et réinitialise le filtre de rééchantillonnage."""
        with self._lock:
            self._buffer.clear()
            if self._resampler is not None:
                self._resampler.clear()
            self._level_history.clear()

