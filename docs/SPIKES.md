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
