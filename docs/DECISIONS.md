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

---

## 2026-09-30 — Architecture du Pipeline Audio et Streaming Direct (Phase 4)

- **Contexte** : L'envoi direct continu du son Windows vers pyatv nécessite d'alimenter `stream_file` sans passer par des fichiers temporaires sur disque.
- **Options évaluées** :
  - *Option A* : Implémentation d'un `PipelineAudioSource` dérivé de `pyatv.protocols.raop.audio_source.AudioSource` lisant directement le ring buffer du pipeline en blocs de 352 frames (1408 octets). Pontage de `open_source` pour retourner directement l'instance sans décodage ni métadonnées bloquantes.
  - *Option B* : Pipe nommé ou `io.BufferedIOBase` avec faux en-tête WAV. Rejetée car TinyTag tente d'analyser la taille totale du flux et bloque sur les flux infinis.
  - *Option C* : Utilisation d'un processus ffmpeg externe. Rejetée car ajoute une dépendance binaire lourde non nécessaire.
- **Décision** :
  - Adoption de **l'Option A** : transmission directe, zéro copie superflue, latence minimale.
  - Utilisation de `soxr.ResampleStream` (qualité MQ) pour le streaming continu 48 kHz -> 44.1 kHz.
  - Ring buffer borné (800 ms cible, 1600 ms max) avec politique de purge des données les plus anciennes en cas d'overflow (priorité au maintien du temps réel).
  - Générateur automatique de silence (production de zéros au débit 44.1 kHz) pour combler les périodes d'inactivité de WASAPI et prévenir la fermeture de la session AirPlay.
