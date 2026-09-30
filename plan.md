# PLAN.md — AirPlay Sender pour Windows (audio système → enceinte Devialet)

> Document destiné à un LLM de code (Claude Code, Cursor, etc.) qui doit implémenter le projet de bout en bout en "vibe coding".
> Lis ce fichier EN ENTIER avant d'écrire la moindre ligne. Travaille phase par phase. Ne saute jamais un spike ni un critère d'acceptation.

---

## 0. Règles de travail pour le LLM (à respecter strictement)

1. **Une phase à la fois.** Ne commence pas la phase N+1 tant que les critères d'acceptation de la phase N ne sont pas validés (exécutés, pas supposés).
2. **Exécute, ne suppose pas.** Chaque critère d'acceptation doit être vérifié en lançant réellement le code (PowerShell sous Windows 11). Si tu ne peux pas vérifier (ex. entendre le son), demande à l'utilisateur de confirmer et dis précisément quoi écouter.
3. **Vérifie les API contre la version installée.** Les API de `pyatv`, `PyAudioWPatch`, etc. évoluent. Avant d'utiliser une fonction, inspecte-la (`help()`, `inspect.signature`, lecture du code dans `.venv\Lib\site-packages`). Ne te fie pas à ta mémoire pour les signatures.
4. **Pas de dépendance inutile.** Reste sur la stack de la §3. Si tu veux en ajouter une, justifie-le dans `docs/DECISIONS.md`.
5. **Journalise tes décisions** dans `docs/DECISIONS.md` (date, contexte, choix, alternatives rejetées). Journalise les résultats de spikes dans `docs/SPIKES.md`.
6. **Petits commits** (si git initialisé) : un commit par tâche logique, message à l'impératif. Ne commite jamais `.venv`, `config.json` local, captures `.pcapng`, fichiers audio de test volumineux.
7. **Demande à l'utilisateur** uniquement pour : modèle exact de l'enceinte, confirmation auditive, choix d'un PIN si le pairing l'exige, autorisation d'installer un outil système (Wireshark/Npcap). Tout le reste, décide toi-même avec un défaut sensible et note-le.
8. **Code lisible et typé** : Python 3.11+, type hints partout, `ruff` + `mypy` (mode non strict au départ), docstrings courtes. Pas de sur-ingénierie : pas de framework d'injection de dépendances, pas de classes abstraites sans 2 implémentations.
9. **Jamais de valeurs en dur** pour IP / nom d'enceinte : tout passe par la config ou la découverte.
10. **Périmètre réseau** : uniquement le LAN local de l'utilisateur, uniquement l'enceinte de l'utilisateur. Aucune action hors réseau local.

---

## 1. Contexte et objectif

### 1.1 Objectif
Application Windows qui capture **tout le son joué par Windows** (WASAPI loopback) et le diffuse en **AirPlay** vers une enceinte **Devialet** (récepteur AirPlay 2 audio, ex. Phantom / Mezzo / Dione), de façon :
- **stable** (plusieurs heures sans coupure),
- **auto-réparatrice** (reconnexion automatique),
- **discrète** (icône dans la zone de notification, démarrage avec Windows),
- **sans configuration lourde** (découverte automatique, pas de Bluetooth).

### 1.2 Pourquoi ce projet
L'outil existant (TuneBlade) fonctionne mais **se coupe / bugue de temps en temps** malgré une licence valide. L'utilisateur veut un outil maison plus fiable, et veut apprendre le protocole (étudiant en cybersécurité, intérêt pour le reverse engineering).

### 1.3 Non-objectifs (ne PAS implémenter)
- Mirroring d'écran / vidéo (FairPlay vidéo) — hors périmètre.
- Fonction de récepteur AirPlay (recevoir du son) — hors périmètre.
- Multi-enceintes synchronisées (multi-room) — **phase bonus optionnelle uniquement**.
- Support macOS / Linux — Windows 11 uniquement.
- Contournement de DRM ou de protection de contenu : on capte la sortie audio du système, rien d'autre.
- Distribution publique / installeur signé — usage personnel.

### 1.4 Environnement cible
- Windows 11 Enterprise (machine possiblement d'entreprise : voir risques réseau §12).
- Shell principal : PowerShell 5.1. Git Bash disponible.
- Python 3.11 ou 3.12 (vérifier ce qui est installé ; sinon proposer l'installation via `winget install Python.Python.3.12`).
- Dossier projet : `C:\Users\<utilisateur>\airplay-sender`.

---

## 2. Connaissances protocolaires à avoir en tête

Résumé pour cadrer l'implémentation (à confirmer par les spikes) :

- **Découverte** : mDNS/Bonjour. Services `_raop._tcp.local.` (audio) et `_airplay._tcp.local.`. Les **TXT records** exposent : `features`/`ft`, `model`/`am`, `pk` (clé publique), `pi`, `flags`/`sf`, `cn` (codecs), `et` (types de chiffrement), `sr`/`ss` (sample rate/size), `tp` (transports), `vs`/`sv` (version), `pw` (mot de passe requis).
- **AirPlay 1 (RAOP)** : RTSP (ANNOUNCE/SETUP/RECORD/SET_PARAMETER/TEARDOWN), audio en RTP (ALAC ou PCM), chiffrement AES, port de contrôle + port de timing UDP.
- **AirPlay 2** : pairing de type HomeKit (SRP + Ed25519 + Curve25519 + ChaCha20-Poly1305 + HKDF), timing PTP, buffered audio stream sur TCP. Beaucoup d'enceintes AirPlay 2 acceptent aussi un **repli de type RAOP / AirPlay 1** avec « transient pairing » (PIN fixe, sans saisie utilisateur).
- **Latence** : le récepteur attend typiquement ~2 s de tampon. Le flux est émis "en avance" et le récepteur rejoue avec un délai fixe → la synchro d'horloge et le ritme d'envoi (pacing) sont critiques.
- **Format audio usuel** : PCM 16 bits, 44,1 kHz, stéréo, encodé ALAC (ou PCM brut selon le cas).

> `pyatv` (Python) implémente côté client : scan, pairing, RAOP (AirPlay audio), contrôle (volume, métadonnées). C'est le point de départ. Sa doc de protocoles (`pyatv.dev/documentation/protocols`) sert de référence.

---

## 3. Stack technique

| Couche | Choix par défaut | Alternative si bloqué |
|---|---|---|
| Langage | Python 3.11/3.12 | Rust ou C# si la boucle temps réel est trop instable |
| Client AirPlay | `pyatv` (dernière version stable) | Implémentation RAOP/RTSP maison (inspirée de shairport-sync / pyatv) |
| Capture audio | `PyAudioWPatch` (PyAudio avec WASAPI loopback) | `soundcard` (attention : incompatibilités numpy 2 selon version) |
| Traitement audio | `numpy` (+ `soxr` ou `samplerate` si rééchantillonnage nécessaire) | `scipy.signal.resample_poly` |
| Async | `asyncio` (pyatv est async) | — |
| Tray UI | `pystray` + `Pillow` | `infi.systray`, ou PySide6 si UI riche voulue |
| Config | fichier JSON + `pydantic` v2 (validation) | `dataclasses` + `json` |
| Logs | `logging` standard + `RotatingFileHandler` | `loguru` |
| CLI | `argparse` (ou `typer`) | — |
| Tests | `pytest`, `pytest-asyncio` | — |
| Qualité | `ruff`, `mypy` | — |
| Packaging | `PyInstaller` (mode `--onedir` d'abord) | `Nuitka` |
| Démarrage auto | clé de registre `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` | dossier Startup |

**Décision d'architecture clé** : tout tourne dans un seul processus, avec :
- un **thread de capture audio** (callback WASAPI → file thread-safe),
- une **boucle asyncio** (pyatv + logique de session),
- un **thread UI tray** (pystray),
communiquant par `queue.Queue` / `asyncio.Queue` via `loop.call_soon_threadsafe`. Ne mélange jamais le callback audio et l'event loop directement.

---

## 4. Structure du dépôt

```
airplay-sender/
├─ PLAN.md                     # ce fichier
├─ README.md                   # usage utilisateur (à rédiger en fin de projet)
├─ pyproject.toml              # métadonnées, deps, config ruff/mypy/pytest
├─ requirements.txt            # deps figées (pip freeze contrôlé)
├─ .gitignore
├─ config.example.json
├─ docs/
│  ├─ DECISIONS.md             # journal des décisions
│  ├─ SPIKES.md                # résultats des expériences
│  ├─ PROTOCOL_NOTES.md        # observations TXT records / Wireshark
│  └─ TROUBLESHOOTING.md
├─ scripts/
│  ├─ 01_scan.py               # phase 1
│  ├─ 02_send_file.py          # phase 2
│  ├─ 03_capture_to_wav.py     # phase 3
│  └─ 04_live_stream.py        # phase 4 (prototype)
├─ src/airplay_sender/
│  ├─ __init__.py
│  ├─ __main__.py              # point d'entrée CLI
│  ├─ config.py                # modèle + chargement/sauvegarde config
│  ├─ logging_setup.py
│  ├─ discovery.py             # scan mDNS, sélection d'enceinte
│  ├─ capture.py               # WASAPI loopback → frames PCM
│  ├─ audio_pipeline.py        # conversion format, rééchantillonnage, silence, ring buffer
│  ├─ streamer.py              # session AirPlay (connexion, stream, volume)
│  ├─ supervisor.py            # machine d'états, reconnexion, backoff
│  ├─ devices_watch.py         # surveillance du changement de sortie audio par défaut
│  ├─ tray.py                  # icône zone de notification + menu
│  ├─ autostart.py             # registre Run
│  ├─ single_instance.py       # mutex Windows
│  └─ metrics.py               # compteurs (underruns, reconnexions, latence)
└─ tests/
   ├─ test_config.py
   ├─ test_audio_pipeline.py
   ├─ test_supervisor.py
   └─ fakes.py                 # faux récepteur / fausse source pour tests
```

---

## 5. Phases détaillées

Chaque phase : **but → tâches → livrables → critères d'acceptation → pièges**.

### Phase 0 — Mise en place (≈ 30 min)

**But** : environnement propre et reproductible.

**Tâches**
1. Vérifier `python --version` (3.11+). Sinon, demander l'accord pour `winget install`.
2. Créer le dossier, `python -m venv .venv`, activer (`.\.venv\Scripts\Activate.ps1`). Si l'exécution de scripts est bloquée : `Set-ExecutionPolicy -Scope Process Bypass` (portée processus uniquement, ne jamais modifier la politique machine).
3. Installer : `pyatv`, `PyAudioWPatch`, `numpy`, `pydantic`, `pystray`, `Pillow`, `pytest`, `pytest-asyncio`, `ruff`, `mypy`. Figer dans `requirements.txt`.
4. Créer `pyproject.toml` (config ruff : line-length 100 ; mypy : `ignore_missing_imports = true`).
5. `git init`, `.gitignore` (`.venv/`, `__pycache__/`, `*.pcapng`, `config.json`, `dist/`, `build/`, `*.wav` sauf `tests/assets/`).
6. Créer `docs/DECISIONS.md` et `docs/SPIKES.md` avec un en-tête.
7. Vérifier si Wireshark + Npcap sont installés ; sinon **demander à l'utilisateur** (installation système, nécessite droits admin).

**Livrables** : venv fonctionnel, `pip list` cohérent, arborescence créée.

**Acceptation** : `python -c "import pyatv, pyaudiowpatch, numpy, pydantic, pystray; print('ok')"` affiche `ok`. `pytest` tourne (même 0 test). `ruff check .` passe.

**Pièges** : `PyAudioWPatch` n'a des wheels que pour certaines versions de Python — si l'installation échoue, essayer Python 3.11/3.12. Ne pas installer `pyaudio` standard en parallèle (conflit de module).

---

### Phase 1 — Découverte réseau (≈ 1 h)

**But** : lister les récepteurs AirPlay du LAN et comprendre ce que la Devialet annonce.

**Tâches**
1. `scripts/01_scan.py` : utiliser `pyatv.scan(loop, timeout=10)` (vérifier la signature installée). Pour chaque appareil : nom, adresse, identifiant, modèle, OS/version, services disponibles (RAOP, AirPlay, Companion…), protocoles, et **propriétés brutes** (TXT records) de chaque service.
2. Afficher en tableau lisible + option `--json` pour sauvegarder la sortie brute dans `docs/PROTOCOL_NOTES.md` (section « Scan initial »).
3. En parallèle, un second scan **indépendant** avec `zeroconf` (`ServiceBrowser` sur `_raop._tcp.local.` et `_airplay._tcp.local.`) pour croiser les résultats (utile si pyatv ne voit rien à cause du pare-feu).
4. Identifier la Devialet : filtrer par nom (argument `--name`) ou interactivement si plusieurs résultats.
5. Documenter dans `PROTOCOL_NOTES.md` : features (bitmask `ft` décodé si possible), `et` (chiffrement), `cn` (codecs), `pw` (mot de passe), présence de `pk`, modèle, version du firmware.

**Livrables** : script fonctionnel + notes de protocole.

**Acceptation** : la Devialet apparaît avec IP + TXT records. Le résultat est copié dans `PROTOCOL_NOTES.md`.

**Pièges**
- mDNS = multicast UDP 5353. Le **Pare-feu Windows** (profil réseau « Public » vs « Privé ») peut bloquer : vérifier que le réseau est en profil Privé, sinon règle entrante UDP 5353 pour Python (demander l'accord avant toute modification de pare-feu).
- Réseaux d'entreprise / Wi-Fi invités : **isolation client** ou filtrage multicast → rien n'est découvert. Dans ce cas, tester depuis un autre réseau ou en Ethernet sur le même VLAN, et le documenter.
- Plusieurs interfaces (VPN, Hyper-V, WSL) : zeroconf peut écouter sur la mauvaise. Permettre de forcer l'interface (`--interface <ip>`).

**Point de décision (noter dans DECISIONS.md)** : d'après les TXT records et le comportement de pyatv, la cible est-elle AirPlay 1/RAOP, AirPlay 2, ou les deux ? Y a-t-il un PIN / mot de passe ?

---

### Phase 2 — Preuve de concept : envoyer un fichier (≈ 2 h)

**But** : valider qu'un son sort de la Devialet depuis Windows, avec pyatv.

**Tâches**
1. `scripts/02_send_file.py --name <enceinte> --file test.wav`.
2. Récupérer la config pyatv de l'appareil via `scan`, sélectionner le service RAOP, `pyatv.connect(config, loop)`.
3. Si un pairing est requis : implémenter `pyatv.pair(config, Protocol.RAOP, loop)` (ou AirPlay), gérer le PIN (demander à l'utilisateur ; pour le « transient pairing » aucun PIN saisi n'est attendu). **Stocker les credentials** dans le fichier de config (clé pyatv `credentials`) — jamais dans les logs.
4. Appeler `atv.stream.stream_file(...)` avec un WAV de test (générer un sinus 440 Hz 5 s + un court morceau, 44,1 kHz 16 bits stéréo).
5. Tester aussi `atv.audio.set_volume(...)` et la lecture du volume (si supporté).
6. Consigner dans `SPIKES.md` : protocole utilisé (RAOP/AirPlay 2), latence perçue avant le premier son, erreurs, formats acceptés (WAV/MP3/FLAC), fonctionnement du volume.
7. **Mesurer** : durée de connexion, temps avant le premier son, stabilité sur un fichier de 10 minutes.

**Livrables** : script de test + `SPIKES.md` complété.

**Acceptation** : l'utilisateur confirme entendre le sinus puis le morceau, sans coupure sur 10 min.

**Pièges**
- `stream_file` pilote lui-même le rythme d'envoi (temps réel). C'est une information cruciale pour la phase 4.
- Si la connexion échoue avec pyatv : capturer avec **Wireshark** un cast depuis un iPhone/Mac vers la même enceinte (filtre : `ip.addr == <ip_enceinte>`), sauvegarder dans `docs/captures/` (ignoré par git), et comparer la séquence RTSP. Documenter dans `PROTOCOL_NOTES.md`.
- Erreurs typiques : `AuthenticationError`, `ConnectionFailedError`, timeouts → consigner le message exact.

**Point de décision** : si ce spike échoue de manière irréparable avec pyatv, basculer sur le **plan B** (§11) avant de continuer.

---

### Phase 3 — Capture du son système (≈ 2 h)

**But** : obtenir un flux PCM propre de ce que Windows joue.

**Tâches**
1. `scripts/03_capture_to_wav.py` : lister les périphériques via `PyAudioWPatch` (`get_default_wasapi_loopback()` ou équivalent ; vérifier l'API de la version installée). Sélectionner le loopback associé à la **sortie par défaut**.
2. Capturer N secondes et écrire un WAV (format natif du périphérique : souvent 48 kHz, 2 canaux, float32 ou int16).
3. Écrire `src/airplay_sender/capture.py` : classe `LoopbackCapture` avec `start()`, `stop()`, `frames` (file `queue.Queue` bornée), propriétés `samplerate`, `channels`, `dtype`. Le callback audio **ne fait que** copier les octets dans la queue (aucune logique lourde, aucun log, aucune allocation inutile).
4. Gérer : périphérique introuvable, changement de format (ex. utilisateur passe en 96 kHz), multi-canaux (5.1/7.1 → downmix stéréo).
5. Comportement **silence** : WASAPI loopback ne produit **aucun callback** quand rien ne joue, selon les configurations. Le pipeline doit donc générer du silence de remplissage (voir phase 4) — documenter ce que tu observes réellement.

**Livrables** : `capture.py` + test manuel + WAV enregistré.

**Acceptation** : le WAV contient fidèlement la musique jouée pendant la capture (à valider à l'écoute avec l'utilisateur). Pas de craquements.

**Pièges**
- Mode exclusif : si une application prend la sortie en mode exclusif, le loopback peut échouer.
- Les « effets audio » / Spatial Sound de Windows peuvent modifier le flux.
- Sortie par défaut qui **change** (casque branché) : à traiter en phase 5.

---

### Phase 4 — Pipeline audio et streaming en direct (≈ 1 à 2 jours) — cœur du projet

**But** : flux continu Windows → enceinte, stable plusieurs heures.

**Tâches**
1. `audio_pipeline.py` :
   - conversion dtype → `int16` little-endian,
   - downmix vers stéréo si besoin,
   - **rééchantillonnage vers 44 100 Hz** si la source est en 48 000 Hz (utiliser `soxr` en mode streaming avec état conservé entre blocs, pas un resample bloc par bloc naïf qui crée des clics),
   - **ring buffer** borné (cible ≈ 500 ms à 1 s), politique d'overflow : jeter les plus anciennes données, compter l'événement,
   - **générateur de silence** : si la queue est vide, produire des zéros pour maintenir le débit constant (évite la fin de session côté enceinte).
2. `streamer.py` : adapter le pipeline à pyatv.
   - **Spike préalable obligatoire** : inspecter `atv.stream.stream_file` dans la version installée pour déterminer les sources acceptées (chemin, `io.BufferedReader`, `asyncio.StreamReader`, objet `AudioSource` personnalisé). Noter la conclusion dans `SPIKES.md`.
   - Option A (préférée) : implémenter une source personnalisée lisant le ring buffer en continu (un « fichier infini » PCM).
   - Option B : écrire le PCM dans un pipe / objet file-like avec un en-tête WAV « streaming » (taille max), lu par pyatv.
   - Option C (si pyatv impose un décodage par fichier) : passer par `ffmpeg` (`-f s16le -ar 44100 -ac 2 -i pipe:` → sortie compatible). N'utiliser que si A et B sont impossibles ; ajoute alors ffmpeg comme dépendance documentée.
3. **Pacing / dérive d'horloge** : la carte son (source) et l'enceinte (destination) ont des horloges légèrement différentes. Implémenter un suivi du niveau de remplissage du ring buffer et, si dérive, un **rééchantillonnage adaptatif** très léger (±50 ppm à ±500 ppm) ou l'ajout/suppression ponctuelle d'un échantillon. Mesurer d'abord si c'est nécessaire (log du niveau du buffer sur 1 h).
4. `scripts/04_live_stream.py` : prototype bout en bout (capture → pipeline → streamer), exécuté en ligne de commande.
5. `metrics.py` : compteurs `underruns`, `overflows`, `reconnects`, niveau moyen du buffer, latence estimée, logués toutes les 30 s au niveau INFO.
6. Test de stabilité : 1 h, puis 4 h, en jouant de la musique réelle + des périodes de silence (pause, changement d'onglet).

**Livrables** : prototype stable + journal de métriques + conclusions dans `SPIKES.md` (latence mesurée, dérive mesurée, besoin ou non du rééchantillonnage adaptatif).

**Acceptation**
- Le son de Windows sort de la Devialet en direct (latence d'environ 1 à 3 s acceptée et documentée).
- 1 h sans coupure ni clic audible ; métriques `underruns` < 1 par heure.
- Les silences (pause) n'interrompent pas la session ; la reprise du son est immédiate.

**Pièges**
- Ne **jamais** bloquer la boucle asyncio avec du calcul lourd : le rééchantillonnage numpy sur des blocs de 20–50 ms est acceptable, mais mesure.
- Le callback audio ne doit jamais attendre un verrou long.
- Éviter le GC Python sur le chemin critique : préallouer les buffers.
- Sur Windows, régler la résolution du timer si nécessaire (`timeBeginPeriod(1)`) pour un pacing régulier — à tester, noter le gain.
- La latence de démarrage du flux peut laisser les premières secondes muettes : documenter (et envisager un pré-remplissage).

---

### Phase 5 — Robustesse et supervision (≈ 1 jour)

**But** : l'outil se répare seul, c'est sa raison d'être.

**Tâches**
1. `supervisor.py` : **machine d'états** explicite :
   `IDLE → DISCOVERING → CONNECTING → STREAMING → RECONNECTING → (ERROR) → STOPPED`
   - transitions journalisées,
   - **backoff exponentiel** (1 s, 2 s, 4 s … plafonné à 30 s) avec jitter,
   - relance de la découverte si l'IP a changé (l'enceinte peut changer d'adresse via DHCP → toujours résoudre par **identifiant/nom mDNS**, pas par IP figée),
   - détection de session morte : timeout sans activité côté pyatv, exceptions de connexion, absence d'ACK.
2. `devices_watch.py` : détecter le changement de **périphérique de sortie par défaut** de Windows (polling toutes les 2 s de l'ID du périphérique par défaut, ou notifications `IMMNotificationClient` via `comtypes` si tu veux être propre). Réaction : redémarrer la capture sur le nouveau loopback sans couper la session AirPlay si possible.
3. Gestion de la mise en veille / reprise de Windows : à la reprise, forcer une reconnexion propre.
4. Gestion de la perte réseau (Wi-Fi coupé, changement de réseau) : pas de crash, état `RECONNECTING`.
5. **Enceinte en veille** : certaines enceintes se mettent en veille réseau et ne répondent plus au mDNS pendant un moment. Prévoir une tentative de réveil (connexion directe par IP connue en dernier recours).
6. Logs : `RotatingFileHandler` dans `%LOCALAPPDATA%\airplay-sender\logs\app.log` (5 × 1 Mo). Niveaux clairs ; aucun secret (credentials, clés) dans les logs.
7. Tests unitaires du superviseur avec un **faux streamer** (`tests/fakes.py`) qui échoue/réussit selon un scénario, et une horloge injectable pour ne pas attendre réellement les backoffs.
8. Test d'endurance : 8 h avec scénarios provoqués (éteindre/rallumer l'enceinte, couper le Wi-Fi 30 s, mettre Windows en veille, brancher/débrancher un casque).

**Livrables** : supervisor + tests + rapport d'endurance dans `SPIKES.md`.

**Acceptation**
- Extinction/rallumage de l'enceinte → reprise automatique du son en < 30 s sans intervention.
- Coupure Wi-Fi de 30 s → reprise automatique.
- Branchement d'un casque puis retour aux haut-parleurs → l'outil suit la sortie par défaut (comportement défini et documenté).
- Aucun crash sur 8 h.

---

### Phase 6 — Configuration, CLI et persistance (≈ 0,5 jour)

**Tâches**
1. `config.py` (pydantic) avec schéma :
   ```json
   {
     "device": { "name": "Devialet Phantom", "identifier": "AA:BB:CC:DD:EE:FF", "last_known_ip": "192.168.1.42" },
     "credentials": { "raop": "<opaque pyatv>" },
     "audio": { "buffer_ms": 800, "target_samplerate": 44100, "capture_device": "default-loopback" },
     "volume": { "sync_with_windows": false, "default": 0.3 },
     "behavior": { "autostart_stream": true, "start_minimized": true, "reconnect": true },
     "logging": { "level": "INFO" }
   }
   ```
   Emplacement : `%APPDATA%\airplay-sender\config.json`. Écriture **atomique** (fichier temporaire + `os.replace`). Permissions : fichier utilisateur uniquement (contient des credentials).
2. CLI (`python -m airplay_sender`) :
   - `scan` — liste les enceintes,
   - `pair` — appairage interactif,
   - `run` — streaming en console (sans UI),
   - `tray` — mode application avec icône,
   - `diag` — rapport de diagnostic (versions, interfaces réseau, résultat du scan, périphériques audio, derniers logs) pour le support,
   - `--config <path>`, `--verbose`.
3. Validation et messages d'erreur clairs (« enceinte introuvable : vérifiez que le PC et l'enceinte sont sur le même réseau »).

**Acceptation** : `scan`, `pair`, `run`, `diag` fonctionnent ; relancer `run` ne redemande pas l'appairage.

---

### Phase 7 — Interface zone de notification (≈ 1 jour)

**Tâches**
1. `tray.py` avec `pystray` : icône avec **3 états visuels** (gris = inactif, orange = connexion, vert = streaming, rouge = erreur). Générer les icônes avec Pillow (pas de fichiers binaires externes).
2. Menu : État (texte non cliquable), Démarrer / Arrêter le streaming, Choisir l'enceinte (sous-menu alimenté par la découverte), Volume (sous-menu de paliers 10/25/50/75/100 %), Démarrage avec Windows (case à cocher), Ouvrir le dossier des logs, Copier le rapport de diagnostic, Quitter.
3. Notifications toast discrètes uniquement sur changements d'état importants (connexion perdue > 10 s, reconnexion réussie).
4. `single_instance.py` : mutex nommé Windows (`CreateMutexW` via `ctypes`) → une seule instance ; si déjà lancée, quitter proprement.
5. `autostart.py` : écriture/suppression de la valeur `AirPlaySender` dans `HKCU\...\Run` avec la commande de lancement (`pythonw.exe -m airplay_sender tray` ou l'exe packagé).
6. Threading : `pystray` dans le thread principal ou un thread dédié (selon le comportement observé), la boucle asyncio dans un autre thread ; communication via `call_soon_threadsafe`. **Aucune** mise à jour UI depuis un thread non autorisé.

**Acceptation** : l'icône reflète l'état réel ; changer d'enceinte via le menu fonctionne ; le démarrage automatique fonctionne après redémarrage de session ; lancer deux fois l'app ne crée pas deux instances.

---

### Phase 8 — Packaging (≈ 0,5 jour)

**Tâches**
1. PyInstaller `--onedir --noconsole` (+ `--console` pour une variante de debug). Inclure les données nécessaires (hooks pour `pyatv`, `zeroconf`, `PyAudioWPatch`). Tester l'exécutable sur une session propre.
2. Icône `.ico` générée via Pillow.
3. Vérifier la taille, le temps de démarrage, l'absence de fenêtre console.
4. Script `build.ps1` reproductible.
5. Mentionner dans `README.md` que l'antivirus peut faire un faux positif sur un exe PyInstaller non signé.

**Acceptation** : `dist\AirPlaySender\AirPlaySender.exe` se lance sans Python installé, découvre l'enceinte et streame.

---

### Phase 9 — Documentation et finitions (≈ 0,5 jour)

- `README.md` : installation, premier lancement, appairage, dépannage rapide, limites connues.
- `docs/TROUBLESHOOTING.md` : arbre de décision (pas d'enceinte trouvée → pare-feu/profil réseau/isolation client ; pas de son → sortie par défaut/mode exclusif ; coupures → Wi-Fi 2,4 GHz, économie d'énergie de la carte réseau ; latence trop élevée → expliquer la limite du tampon AirPlay).
- Nettoyage : `ruff`, `mypy`, couverture de tests des modules purs (`config`, `audio_pipeline`, `supervisor`).
- Mise à jour de `DECISIONS.md`.

---

### Phase bonus (optionnelle, seulement si tout le reste est validé)

- Synchronisation du volume Windows → enceinte.
- Métadonnées (titre de la fenêtre du lecteur média via SMTC `Windows.Media.Control`) envoyées à l'enceinte.
- Multi-enceintes (groupe).
- Égaliseur logiciel simple avant envoi.
- Petite page de statut locale (HTTP sur 127.0.0.1).

---

## 6. Spécifications d'interfaces (contrats entre modules)

```python
# capture.py
class LoopbackCapture:
    samplerate: int
    channels: int
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def read(self, timeout: float) -> bytes | None: ...   # bloc PCM natif, None si timeout

# audio_pipeline.py
class AudioPipeline:
    def __init__(self, in_rate: int, in_channels: int, out_rate: int = 44100, buffer_ms: int = 800): ...
    def push(self, raw: bytes) -> None: ...               # appelé depuis le thread capture
    def pull(self, nbytes: int) -> bytes: ...             # renvoie toujours nbytes (silence si vide)
    @property
    def level_ms(self) -> float: ...                      # remplissage courant
    stats: PipelineStats                                  # underruns, overflows, etc.

# streamer.py
class AirPlayStreamer:
    async def connect(self, device: DeviceRef, credentials: str | None) -> None: ...
    async def stream(self, source: AudioPipeline) -> None: ...  # ne retourne que sur arrêt/erreur
    async def set_volume(self, level: float) -> None: ...
    async def close(self) -> None: ...

# supervisor.py
class Supervisor:
    state: State
    async def run(self) -> None: ...                      # boucle principale
    def request_start(self) / request_stop(self) / request_switch_device(self, ref) -> None: ...
    on_state_change: Callable[[State], None]
```

Règles : les frontières thread/asyncio passent uniquement par `AudioPipeline.push/pull` (thread-safe, non bloquant) et `call_soon_threadsafe`.

---

## 7. Gestion d'erreurs (catalogue)

| Erreur | Cause probable | Réaction |
|---|---|---|
| Aucun appareil trouvé | Pare-feu, VLAN, enceinte en veille | Retry + message explicite + `diag` |
| `AuthenticationError` | Credentials périmés/invalides | Supprimer les credentials, relancer le pairing (demander à l'utilisateur) |
| Timeout de connexion | Enceinte éteinte, Wi-Fi | Backoff, état RECONNECTING |
| Déconnexion en cours de stream | Coupure réseau, veille enceinte | Reconnexion auto |
| Périphérique de capture disparu | Casque débranché, pilote | Rebasculer sur la sortie par défaut |
| Buffer underrun répété | Machine surchargée, Wi-Fi saturé | Augmenter `buffer_ms` temporairement, logguer |
| Format audio changé | Changement de fréquence dans Windows | Reconfigurer le pipeline à chaud |

Jamais de `except Exception: pass`. Toute exception attrapée est journalisée avec sa trace (niveau DEBUG/ERROR selon le cas).

---

## 8. Sécurité et vie privée

- Les **credentials d'appairage** sont stockés dans `%APPDATA%` (accès utilisateur) ; jamais dans le dépôt, jamais dans les logs (masquer dans `diag`).
- L'application n'ouvre aucun port en écoute vers l'extérieur (sauf ports éphémères nécessaires au protocole, côté client).
- Aucune télémétrie, aucune connexion Internet.
- Ne capture que la sortie audio système ; ne jamais enregistrer sur disque hors mode debug explicite, et purger les fichiers de debug.
- Les captures Wireshark contiennent des identifiants de l'enceinte : ne pas les commiter.

---

## 9. Tests

- **Unitaires** (sans matériel) : `config` (validation, écriture atomique), `audio_pipeline` (conversion, rééchantillonnage sans discontinuité, ring buffer, silence), `supervisor` (toutes les transitions avec faux streamer et horloge injectable).
- **Test du rééchantillonnage** : générer un sinus 1 kHz en 48 kHz, le passer par le pipeline, vérifier par FFT que la fréquence reste 1 kHz et qu'il n'y a pas de pics parasites aux frontières de blocs.
- **Intégration manuelle** (avec enceinte) : checklist dans `docs/TROUBLESHOOTING.md` (scan, pairing, lecture, coupure enceinte, coupure Wi-Fi, veille Windows, changement de sortie).
- **Endurance** : scripts lançant 4–8 h avec journal des métriques.

---

## 10. Critères de succès globaux (Definition of Done)

- [ ] `AirPlaySender.exe` lancé → icône verte → le son de Windows sort de la Devialet.
- [ ] 8 h d'utilisation sans intervention, `underruns` < 1/h.
- [ ] Reconnexion automatique après extinction de l'enceinte, coupure Wi-Fi, veille Windows.
- [ ] Démarrage automatique avec Windows, instance unique.
- [ ] Appairage mémorisé (pas redemandé).
- [ ] Tests unitaires verts, `ruff` et `mypy` passent.
- [ ] Documentation à jour (README, DECISIONS, SPIKES, TROUBLESHOOTING).

---

## 11. Plan B si pyatv ne suffit pas

1. **Analyse Wireshark** d'un cast iPhone/Mac → Devialet : séquence exacte (découverte, `/info`, pair-setup/pair-verify, SETUP, RECORD, flux RTP/TCP), enregistrée dans `PROTOCOL_NOTES.md`.
2. **Comparer** avec le comportement de `pyatv` (activer ses logs DEBUG) pour localiser la divergence.
3. Implémenter un **client RAOP minimal** (AirPlay 1 avec repli AirPlay 2 transient) : RTSP (`OPTIONS`, `ANNOUNCE` ou `SETUP` AirPlay 2, `RECORD`, `SET_PARAMETER`, `TEARDOWN`), RTP audio PCM/ALAC chiffré, serveur de timing UDP. Références : code de `pyatv`, de `shairport-sync` et de `airplay2-receiver` (côté récepteur : ils montrent ce que l'enceinte attend).
4. Si Python est le facteur limitant (jitter), porter la boucle d'envoi en **Rust** (crate `tokio`, `cpal`/WASAPI) ou **C#** (`NAudio` + sockets), en gardant la même architecture.
5. **Ne pas** essayer de contourner FairPlay : l'audio vers les enceintes ne le requiert pas dans les modes considérés ici.

---

## 12. Risques et mitigations

| Risque | Probabilité | Mitigation |
|---|---|---|
| Réseau d'entreprise filtrant le multicast / isolant les clients | Moyenne (PC Enterprise) | Tester tôt (phase 1) ; Ethernet ; réseau domestique ; fallback connexion directe par IP si connue |
| Pare-feu Windows bloquant mDNS | Moyenne | Profil réseau Privé ; règle explicite (avec accord de l'utilisateur) |
| Droits limités (pas d'admin, installation de Wireshark impossible) | Moyenne | Travailler avec les logs DEBUG de pyatv ; capturer sur un autre poste |
| pyatv incompatible avec le firmware de la Devialet | Faible/Moyenne | Plan B §11 |
| Dérive d'horloge → coupures | Moyenne | Rééchantillonnage adaptatif, mesure en phase 4 |
| Jitter Python | Moyenne | Préallocation, threads dédiés, timer 1 ms, portage Rust en dernier recours |
| Antivirus d'entreprise bloquant l'exe PyInstaller | Moyenne | Exécuter via `pythonw -m` depuis le venv, documenter |
| Évolution du firmware Devialet/Apple cassant le protocole | Faible | Logs DEBUG + mode `diag`, mise à jour de pyatv |

---

## 13. Prompts de démarrage suggérés (pour l'utilisateur)

**Lancement global**
> Lis `PLAN.md` en entier. Commence par la phase 0, puis arrête-toi et montre-moi les critères d'acceptation validés avant de passer à la phase 1. Respecte la section 0.

**Pour chaque phase**
> Implémente la phase N de `PLAN.md`. Exécute réellement le code pour valider les critères d'acceptation. Mets à jour `docs/SPIKES.md` et `docs/DECISIONS.md`. Si un point de décision apparaît, recommande une option et continue avec, sauf si c'est bloquant.

**En cas de blocage**
> Active les logs DEBUG de pyatv, reproduis l'erreur, et propose trois hypothèses classées par probabilité avec le test qui départage chacune. Ne change pas d'architecture sans l'écrire dans `DECISIONS.md`.

---

## 14. Annexe — Commandes utiles (PowerShell)

```powershell
# Environnement
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install pyatv PyAudioWPatch numpy pydantic pystray Pillow pytest pytest-asyncio ruff mypy soxr zeroconf

# Qualité
ruff check . ; mypy src ; pytest -q

# Réseau
Get-NetConnectionProfile                     # profil Public/Privé
Get-NetIPConfiguration                       # interfaces
Get-NetFirewallRule -DisplayName "*python*"  # règles existantes (lecture seule)

# Audio
Get-PnpDevice -Class AudioEndpoint | Select Status, FriendlyName

# Packaging
pip install pyinstaller
pyinstaller --noconfirm --onedir --noconsole --name AirPlaySender src\airplay_sender\__main__.py
```

---

## 15. Ordre d'exécution et estimation

| Phase | Durée | Dépend de | Risque |
|---|---|---|---|
| 0 Mise en place | 0,5 h | — | Faible |
| 1 Découverte | 1 h | 0 | Moyen (réseau) |
| 2 Fichier → enceinte | 2 h | 1 | **Élevé (go/no-go)** |
| 3 Capture | 2 h | 0 | Faible |
| 4 Streaming live | 1–2 j | 2, 3 | **Élevé** |
| 5 Robustesse | 1 j | 4 | Moyen |
| 6 Config/CLI | 0,5 j | 4 | Faible |
| 7 Tray | 1 j | 5, 6 | Moyen |
| 8 Packaging | 0,5 j | 7 | Moyen |
| 9 Docs | 0,5 j | tout | Faible |

Les phases 1–2 sont le **go/no-go** du projet : si elles échouent, basculer sur le plan B avant d'investir dans le reste. Les phases 2 et 3 peuvent être menées en parallèle.
