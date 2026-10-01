"""Tests unitaires pour le pipeline audio (audio_pipeline.py)."""

from __future__ import annotations

import numpy as np

from airplay_sender.audio_pipeline import AudioPipeline


def test_pipeline_silence_generation() -> None:
    """Vérifie que pull() renvoie du silence et maintient le débit sans bloquer."""
    pipeline = AudioPipeline(in_rate=48000, in_channels=2, out_rate=44100, buffer_ms=500)
    assert pipeline.level_ms == 0.0

    chunk = pipeline.pull(1408)
    assert len(chunk) == 1408
    assert chunk == b"\x00" * 1408
    # Tampon en préremplissage : ce n'est pas un underrun
    assert pipeline.stats.underruns == 0
    assert pipeline.stats.silence_bytes_generated == 1408


def test_pipeline_prefill_then_rebuffer_after_underrun() -> None:
    """Le tampon joue du silence jusqu'à sa cible, puis re-remplit après un underrun."""
    pipeline = AudioPipeline(in_rate=44100, in_channels=2, out_rate=44100, buffer_ms=100)
    nbytes = 1408
    audio = b"\x07\x00" * (nbytes // 2)

    # Sous la cible : silence malgré les données disponibles, sans underrun
    pipeline.push(audio * 5)
    assert pipeline.pull(nbytes) == b"\x00" * nbytes
    assert pipeline.stats.underruns == 0

    # Cible atteinte : lecture normale
    pipeline.push(audio * (pipeline.target_buffer_bytes // nbytes + 1))
    assert pipeline.pull(nbytes) == audio

    # Vider le tampon : un seul underrun, puis retour en préremplissage
    pipeline.pull(len(pipeline._buffer))
    assert pipeline.pull(nbytes) == b"\x00" * nbytes
    assert pipeline.stats.underruns == 1
    pipeline.push(audio * 3)
    assert pipeline.pull(nbytes) == b"\x00" * nbytes
    assert pipeline.stats.underruns == 1


def test_pipeline_clear_resets_prefill() -> None:
    pipeline = AudioPipeline(in_rate=44100, in_channels=2, out_rate=44100, buffer_ms=10)
    audio = b"\x07\x00" * 4000
    pipeline.push(audio)
    assert pipeline.pull(1408) == audio[:1408]

    pipeline.clear()
    pipeline.push(audio[:1408])
    assert pipeline.pull(1408) == b"\x00" * 1408


def test_pipeline_pass_through_when_matching_rate() -> None:
    """Vérifie le fonctionnement sans rééchantillonnage quand in_rate == out_rate."""
    # cible 352 o <= 400 o <= plafond 705 o
    pipeline = AudioPipeline(in_rate=44100, in_channels=2, out_rate=44100, buffer_ms=2)
    data = b"\x01\x00\x02\x00" * 100
    pipeline.push(data)
    assert pipeline.level_ms > 0

    pulled = pipeline.pull(len(data))
    assert pulled == data


def test_pipeline_overflow_policy() -> None:
    """Vérifie que les débordements de file jettent les anciennes données et sont comptabilisés."""
    pipeline = AudioPipeline(in_rate=44100, in_channels=2, out_rate=44100, buffer_ms=100)
    max_bytes = pipeline.max_buffer_bytes

    # Pousser 3 fois la capacité maximale
    huge_data = b"\x05" * (max_bytes * 3)
    pipeline.push(huge_data)

    assert pipeline.stats.overflows > 0
    # La taille du tampon doit rester maîtrisée
    assert len(pipeline._buffer) <= max_bytes


def test_pipeline_set_buffer_ms() -> None:
    """Vérifie le redimensionnement dynamique du tampon."""
    pipeline = AudioPipeline(in_rate=44100, in_channels=2, out_rate=44100, buffer_ms=200)
    data = b"\x01\x00\x02\x00" * 4000
    pipeline.push(data)
    assert pipeline.level_ms > 50

    pipeline.set_buffer_ms(40)
    assert pipeline.buffer_ms == 40
    assert len(pipeline._buffer) <= pipeline.max_buffer_bytes


def test_pipeline_resampling_1khz_fft() -> None:
    """Vérifie le rééchantillonnage 48 kHz -> 44.1 kHz avec analyse spectrale FFT.

    Exigence explicite de PLAN.md (§9).
    """
    sr_in = 48000
    sr_out = 44100
    dur = 1.0
    freq_target = 1000.0

    pipeline = AudioPipeline(
        in_rate=sr_in,
        in_channels=2,
        out_rate=sr_out,
        buffer_ms=600,
    )

    t = np.linspace(0, dur, int(sr_in * dur), endpoint=False)
    sine = (np.sin(2 * np.pi * freq_target * t) * 20000).astype(np.int16)
    stereo_in = np.column_stack((sine, sine)).tobytes()

    # Découpage par blocs de 10 ms (480 échantillons par canal)
    chunk_bytes = 480 * 2 * 2
    for i in range(0, len(stereo_in), chunk_bytes):
        pipeline.push(stereo_in[i : i + chunk_bytes])

    # Extraction des échantillons convertis (en sautant les premiers ms transitoires)
    total_out_bytes = int(dur * sr_out * 4)
    out_bytes = pipeline.pull(total_out_bytes)
    assert len(out_bytes) == total_out_bytes

    samples_out = np.frombuffer(out_bytes, dtype=np.int16)[::2]  # Canal gauche
    # Ignorer les 100 premiers ms de chauffe du filtre
    valid_samples = samples_out[int(0.1 * sr_out) :]

    fft_spectrum = np.abs(np.fft.rfft(valid_samples))
    freq_bins = np.fft.rfftfreq(len(valid_samples), 1.0 / sr_out)
    measured_peak_freq = freq_bins[np.argmax(fft_spectrum)]

    # La fréquence crête doit être précisément à 1000 Hz ± 2 Hz
    assert abs(measured_peak_freq - freq_target) < 2.0
    assert 0.5 < pipeline.stats.current_peak < 0.7


def test_pipeline_volume_scaling() -> None:
    """Vérifie l'atténuation logicielle du signal audio selon le volume défini."""
    # cible 1058 o <= 2000 o <= plafond 2116 o
    pipeline = AudioPipeline(in_rate=44100, in_channels=2, out_rate=44100, buffer_ms=6)
    samples = np.full(1000, 10000, dtype=np.int16)
    data = samples.tobytes()

    # 1. Volume à 50%
    pipeline.set_volume_scale(0.5)
    assert pipeline.volume_scale == 0.5
    pipeline.push(data)

    out_50 = pipeline.pull(len(data))
    samples_out_50 = np.frombuffer(out_50, dtype=np.int16)
    assert np.allclose(samples_out_50, 5000, atol=2)

    # 2. Mute (volume 0.0)
    pipeline.set_volume_scale(0.0)
    pipeline.push(data)
    out_mute = pipeline.pull(len(data))
    samples_out_mute = np.frombuffer(out_mute, dtype=np.int16)
    assert np.all(samples_out_mute == 0)
    assert pipeline.stats.current_peak == 0.0
