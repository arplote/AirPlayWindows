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

---

## 2026-09-30 — Supervision résiliente, Machine d'États et Auto-réparation (Phase 5)

- **Contexte** : Assurer une continuité de service ininterrompue face aux instabilités réseau, coupures d'enceinte et changements de sortie audio sous Windows.
- **Machine d'états explicite** :
  - États : `IDLE`, `DISCOVERING`, `CONNECTING`, `STREAMING`, `RECONNECTING`, `STOPPED`.
  - Toute exception réseau (déconnexion socket, perte RTSP/RAOP) bascule en état `RECONNECTING`.
- **Politique de reconnexion** :
  - Backoff exponentiel : base 1.0s, facteur 2.0x, plafonné à 30.0s max, avec full jitter aléatoire `random.uniform(0, delay)`.
  - Pas d'abandon silencieux : tentatives continues tant que le superviseur n'est pas explicitement arrêté.
- **Gestion des périphériques audio (WASAPI)** :
  - `AudioDeviceWatcher` surveille le périphérique loopback par défaut sous Windows en arrière-plan (polling 2s).
  - En cas de changement (ex: débranchement ou branchement d'un casque / carte son externe), la capture WASAPI est réinitialisée à chaud sans interrompre la session AirPlay (le générateur de silence comble l'interruption transitoire).
- **Observabilité et Logging** :
  - Logs rotatifs dans `%LOCALAPPDATA%\airplay-sender\logs\app.log` (5 Mo x 5 fichiers).
  - Filtre de masquage `SensitiveDataFilter` pour anonymiser toute clé, token ou mot de passe dans les traces.

---

## 2026-09-30 — Configuration Pydantic, CLI et Diagnostic (Phase 6)

- **Contexte** : Fournir une interface utilisateur en ligne de commande unifiée (`python -m airplay_sender`), stocker la configuration de manière sécurisée et robuste, et fournir un outil d'assistance pour le support.
- **Modélisation et validation des données** :
  - Pydantic v2 pour la sérialisation, validation des types et bornes (buffer entre 100 et 5000 ms, volume entre 0.0 et 1.0, samplerate 44100 ou 48000 Hz).
  - Fichier stocké par défaut dans `%APPDATA%\airplay-sender\config.json`.
- **Écriture atomique** :
  - Écriture d'abord dans un fichier temporaire (`tempfile.mkstemp` dans le même dossier parent), puis remplacement atomique via `os.replace` pour éviter toute corruption en cas d'interruption ou coupure électrique.
- **Diagnostic intégré (`diag`)** :
  - `diagnostics.py` synthétise l'environnement (OS, Python, interfaces réseau, loopbacks WASAPI détectés, scan mDNS et 50 dernières lignes de logs avec credentials masqués).
  - Utilisable à la fois en ligne de commande (`python -m airplay_sender diag`) et directement exportable dans la future interface graphique du System Tray.

---

## 2026-09-30 — Interface System Tray, Instance Unique et Autostart (Phase 7)

- **Contexte** : Offrir une présence discrète dans la zone de notification Windows sans fenêtre console envahissante, avec contrôle rapide du volume, sélection d'enceinte et sécurité d'instance unique.
- **Modèle de threading** :
  - `pystray.Icon.run()` s'exécute sur le thread principal pour alimenter la boucle de messages Win32.
  - La boucle événementielle `asyncio` s'exécute dans un thread d'arrière-plan dédié (`AsyncioThread`).
  - Toutes les interactions utilisateur (menu → actions réseau) sont déléguées via `asyncio.run_coroutine_threadsafe`.
  - Toutes les notifications d'état (superviseur → icône) actualisent l'icône, le tooltip et le menu via `call_soon_threadsafe` / `update_menu()`.
- **Génération vectorielle des icônes (`icons.py`)** :
  - Zéro dépendance sur des fichiers image externes. Pillow génère dynamiquement des icônes vectorisées en 4 couleurs (gris inactif, orange connexion, vert streaming, rouge erreur) et exporte un `.ico` multi-résolution (16 à 256 px).
- **Instance unique (`single_instance.py`)** :
  - Utilisation d'un Mutex nommé Win32 (`Local\AirPlaySender_SingleInstance_Mutex` via `ctypes.windll.kernel32.CreateMutexW`).
  - Détection immédiate du code `ERROR_ALREADY_EXISTS` (183) : toute seconde tentative de lancement quitte immédiatement et silencieusement.
- **Démarrage automatique (`autostart.py`)** :
  - Clé de registre `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\AirPlaySender`.
  - Utilise `pythonw.exe` en mode développement ou l'exécutable PyInstaller en mode packagé, évitant l'ouverture intempestive d'une fenêtre console au démarrage de Windows.



