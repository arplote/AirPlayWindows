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
  - **Validation auditive utilisateur** : Confirmée.

---

## Phase 3 — Capture du son système Windows (WASAPI Loopback)

- **Date** : 2026-09-30
- **Scripts / Modules créés** :
  - `src/airplay_sender/capture.py` (`LoopbackCapture`)
  - `scripts/03_capture_to_wav.py`
  - `tests/test_capture.py`
- **Périphérique détecté** : `Haut-parleurs (4- SoundWire Audio) [Loopback]` (index 10)
- **Format audio natif** :
  - Fréquence : `48 000 Hz`
  - Canaux : `2` (stéréo)
  - Dtype : `int16`
- **Comportement silence (critique)** :
  - **Constat** : Lorsque Windows n'émet aucun flux sonore, WASAPI loopback ne produit **strictement aucun callback** (0 paquet / 0 octet reçu).
  - Dès qu'un flux démarre, les paquets arrivent à cadence régulière (~10 ms par bloc, 548 blocs pour 6 secondes).
  - Débordements observés : 0.
- **Vérification de la fidélité audio** :
  - Enregistrement de 5.48 s de son système (`tests/assets/recorded_loopback.wav`).
  - Signal capturé : niveau RMS de 3348, crête à 35.1% de la dynamique int16, aucune saturation ni coupure.

---

## Phase 4 — Pipeline audio et streaming en direct (temps réel)

- **Date** : 2026-09-30
- **Spike préalable obligatoire (API pyatv stream_file)** :
  - `stream_file` attend `Union[str, io.BufferedIOBase, asyncio.streams.StreamReader]`.
  - Pour les objets de type flux mémoire, pyatv utilise `tinytag` (lecture d'en-tête et métadonnées) et `miniaudio.stream_any` pour décoder. Les pipes PCM infinis provoquent des blocages en recherche de fin de fichier par TinyTag.
  - En revanche, la couche interne `StreamClient.send_audio` accepte nativement un objet dérivant de `pyatv.protocols.raop.audio_source.AudioSource` implémentant `readframes(nframes: int) -> bytes` (352 frames = 1408 octets par paquet).
  - **Décision (Option A)** : Implémentation de `PipelineAudioSource(AudioSource)` connecté directement à l'anneau tampon (`AudioPipeline.pull`), avec pontage transparent d'`open_source`. Zéro fichier temporaire, zéro décodage inutile, latence minimale.
- **Vérification du rééchantillonneur (`soxr`)** :
  - Test spectral FFT : injection d'un sinus 1000 Hz à 48000 Hz dans le pipeline, analyse FFT sur la sortie rééchantillonnée à 44100 Hz.
  - Fréquence crête mesurée : **1000.0 Hz** exacte, aucun artefact spectral, test unitaire validé.
- **Gestion du silence & Ring Buffer** :
  - Tampon cible : 800 ms (max 1600 ms).
  - Génération de silence automatique : lorsque la carte son est muette (0 callback WASAPI), le pipeline injecte des zéros au rythme exact de 44.1 kHz, évitant toute fermeture de session côté enceinte.
- **Test d'intégration en direct (`scripts/04_live_stream.py`)** :
  - Connexion automatique à l'enceinte Devialet en RAOP.
  - Capture en direct du son Windows avec bascule immédiate du silence à la musique et retour au silence.
  - Session testée avec 320 640 octets réels capturés et 2 389 376 octets diffusés sans aucune coupure, puis arrêt propre de la session.

---

## Phase 5 — Robustesse, Machine d'États et Auto-réparation

- **Date** : 2026-09-30
- **Validation de la machine d'états (`Supervisor`)** :
  - Cycle de vie complet validé : `IDLE -> DISCOVERING -> CONNECTING -> STREAMING -> STOPPED`.
  - Reconnexion automatique avec backoff exponentiel et jitter : simulation d'échecs de connexion et de coupures de stream à chaud via `FakeStreamer` (test unitaire `test_supervisor_reconnect_on_stream_failure`).
  - Détection de changement de sortie audio Windows (`AudioDeviceWatcher`) : test unitaire `test_supervisor_device_change_resets_capture` vérifiant l'arrêt de l'ancienne capture et le redémarrage immédiat sans perte de flux.
- **Validation du logging et sécurité (`logging_setup`)** :
  - Rotation effective vérifiée sur fichier temporaire.
  - Masquage des identifiants (mots de passe, tokens, secrets de pairing) dans les messages et arguments de logs validé par `test_logging.py`.

---

## Phase 6 — Configuration, CLI et Persistance

- **Date** : 2026-09-30
- **Validation CLI & Persistance** :
  - `python -m airplay_sender scan --timeout 3 --save "Phantom"` : découverte de l'enceinte Devialet Phantom II (`192.168.1.42:7000`) et persistance atomique dans `%APPDATA%\airplay-sender\config.json`.
  - `python -m airplay_sender pair` : vérification que l'enceinte utilise RAOP sans appairage PIN obligatoire (`PairingRequirement.NotNeeded`), confirmation utilisateur sans blocage.
  - `python -m airplay_sender diag` : génération complète du rapport d'environnement avec masquage des identifiants sensibles.
  - `python -m airplay_sender run` : reprise immédiate de la cible configurée, transition automatique `IDLE -> DISCOVERING -> CONNECTING -> STREAMING` sans redemander d'appairage.

---

## Phase 7 — Interface System Tray, Instance Unique et Autostart

- **Date** : 2026-09-30
- **Validation System Tray (`tray.py`)** :
  - Génération dynamique des icônes Pillow 64x64 validée pour tous les états (`IDLE`, `DISCOVERING`, `CONNECTING`, `STREAMING`, `ERROR`).
  - Menu contextuel complet : statut non cliquable, toggle streaming, liste dynamique des appareils découverts, réglages de volume, démarrage avec Windows, dossier de logs, copie du rapport de diagnostic dans le presse-papier, quitter.
  - Instance unique (`single_instance.py`) : Mutex Win32 testé avec succès, rejet immédiat de la seconde instance avec détection `ERROR_ALREADY_EXISTS`.
  - Gestion du registre Windows (`autostart.py`) : intégration dans `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.

---

## Phase 8 — Packaging Autonome (PyInstaller)

- **Date** : 2026-09-30
- **Validation du packaging (`build.ps1`)** :
  - Génération de l'icône multi-résolution `assets/icon.ico` (20 Ko, 16 à 256 px).
  - Compilation complète avec PyInstaller 6.22 en mode `--onedir` et `--noconsole` (durée de compilation : 27 secondes).
  - Test d'exécution directe de l'exécutable autonome `dist\AirPlaySender\AirPlaySender.exe` :
    - `AirPlaySender.exe --help` : exécution instantanée avec aide complète.
    - `AirPlaySender.exe diag` : exécution complète sans console intempestive, collecte des loopbacks WASAPI et interfaces réseau fonctionnelle.





