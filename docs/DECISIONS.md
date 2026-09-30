# Journal des Décisions d'Architecture

Ce document consigne les choix structurants, le contexte, les options étudiées et les justifications relatives au projet `airplay-sender`.

---

## 2026-09-30 — Initialisation du projet et choix de la stack

- **Contexte** : Mise en place de l'application de capture audio Windows 11 vers récepteur AirPlay (Devialet Phantom / Mezzo / Dione).
- **Décision** :
  - Python 3.13 détecté et validé avec `PyAudioWPatch` (0.2.12.8) et `pyatv` (0.18.0).
  - Utilisation de `soxr` pour le rééchantillonnage streaming sans clics.
  - Architecture monoprocessus multithreadée : thread de capture WASAPI loopback, boucle événementielle asyncio pour pyatv et thread UI dédié pour pystray.
- **Alternatives rejetées** :
  - soundcard : conflits possibles avec numpy 2 et moins spécialisé sur le loopback WASAPI sous Windows que PyAudioWPatch.
  - Rust / C# : réservé en plan B si la boucle temps réel présente du jitter rédhibitoire.
