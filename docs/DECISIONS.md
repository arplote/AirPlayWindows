# Journal des Décisions d'Architecture

Ce document consigne les choix structurants, le contexte, les options étudiées et les justifications relatives au projet `airplay-sender`.

---

## 2026-09-30 — Initialisation du projet et choix de la stack (Phase 0)

- **Contexte** : Mise en place de l'application de capture audio Windows 11 vers récepteur AirPlay (Devialet Phantom / Mezzo / Dione).
- **Décision** :
  - Python 3.13 détecté et validé avec `PyAudioWPatch` (0.2.12.8) et `pyatv` (0.18.0).
  - Utilisation de `soxr` pour le rééchantillonnage streaming sans clics.
  - Architecture monoprocessus multithreadée : thread de capture WASAPI loopback, boucle événementielle asyncio pour pyatv et thread UI dédié pour pystray.
- **Alternatives rejetées** :
  - soundcard : conflits possibles avec numpy 2 et moins spécialisé sur le loopback WASAPI sous Windows que PyAudioWPatch.
  - Rust / C# : réservé en plan B si la boucle temps réel présente du jitter rédhibitoire.

---

## 2026-09-30 — Protocole cible et authentification (Phase 1)

- **Contexte** : Analyse des services découverts sur l'enceinte Devialet Phantom II 98 dB (`192.168.1.42:7000`).
- **Analyse des TXT records** :
  - Annonce simultanée des services `_airplay._tcp.local.` et `_raop._tcp.local.`.
  - Service RAOP :
    - `tp=UDP` (transport UDP).
    - `cn=0,1` (codecs PCM 16/44.1 et ALAC).
    - `et=0,4` (chiffrement : 0 = None/Clear text PCM supporté, 4 = FairPlay v2).
    - `pw=False` (aucun mot de passe requis).
  - Service AirPlay :
    - `pairing_requirement=PairingRequirement.NotNeeded`.
    - Features bitmask (`0x445F8A00,0x1C340`) inclut le bit 38 (`TransientPairing`), bit 11 (`AudioStream`), bit 20 (`BufferedAudio`).
- **Décision** :
  - La Devialet supporte à la fois **RAOP (AirPlay 1 audio)** et **AirPlay 2**.
  - **Stratégie prioritaire** : Utiliser le service **RAOP** via `pyatv` (service port 7000), sans PIN/mot de passe requis, avec transport UDP et codec ALAC ou PCM 16/44.1.
  - Aucun appairage complexe SRP requis (`Pairing: NotNeeded`).

