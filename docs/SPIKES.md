# Résultats des Spikes et Expérimentations

Ce document contient les résultats empiriques, mesures de performance, temps de latence et analyses issues des scripts de test.

---

## Phase 0 — Environnement et validation des dépendances

- **Date** : 2026-09-30
- **Version Python** : 3.13.7 (64-bit) sous Windows 11
- **Dépendances clés installées** :
  - `pyatv` : 0.18.0
  - `PyAudioWPatch` : 0.2.12.8 (wheel Windows AMD64 cp313 fonctionnelle)
  - `numpy` : 2.5.3
  - `soxr` : 1.1.0
  - `zeroconf` : 0.151.5
  - `pydantic` : 2.13.5
  - `pystray` : 0.19.5
  - `Pillow` : 12.3.0
- **Observations** :
  - L'import simultané de toutes les dépendances clés réussit sans conflit.

---

## Phase 1 — Découverte réseau et analyse protocolaire

- **Date** : 2026-09-30
- **Script exécuté** : `scripts/01_scan.py`
- **Résultats** :
  - Appareil détecté : Devialet Phantom II 98 dB (`Phantom II 98 dB-1a2b`)
  - IP : `192.168.1.42`, Port : `7000`
  - MAC : `AA:BB:CC:DD:EE:FF`
  - Services actifs : `_airplay._tcp.local.` et `_raop._tcp.local.`
  - Appairage : `PairingRequirement.NotNeeded`, `requires_password: False`
  - Codecs supportés (`cn`) : `0` (PCM 16/44.1 kHz), `1` (ALAC)
  - Chiffrement supporté (`et`) : `0` (None / Clair), `4` (FairPlay v2)
  - Transport (`tp`) : UDP

---

## Phase 2 — Preuve de concept : envoi d'un fichier audio

- **Date** : 2026-09-30
- **Script exécuté** : `scripts/02_send_file.py`
- **Protocole testé** : `RAOP` et `AirPlay` (via `pyatv`)
- **Mesures empiriques** :
  - **Durée de connexion** : < 0.05 seconde (quasi instantanée après scan)
  - **Appairage** : Aucun mot de passe / code PIN requis (`PairingRequirement.NotNeeded`).
  - **Contrôle de volume** :
    - Propriété `atv.audio.volume` fonctionnelle (lecture exacte, ex: `33.0%`).
    - Méthode `atv.audio.set_volume(level)` fonctionnelle (testé avec succès à `35.0%` puis restauré à `33.0%`).
  - **Latence de lecture (pacing)** :
    - Fichier sinus 5.0 s (`tests/assets/sine_440hz_5s.wav`) diffusé en 7.20 s.
    - Fichier mélodique 8.0 s (`tests/assets/melody_test.wav`) diffusé en 10.18 s.
    - Écart constant observé de ≈ 2.15 à 2.20 secondes, correspondant exactement au buffer de jitter de 2 secondes attendu par le récepteur AirPlay 1/2.
  - **Formats testés** : WAV PCM 16 bits, 44.1 kHz, stéréo.
  - **Erreurs observées** : Aucune exception réseau ou protocolaire.
