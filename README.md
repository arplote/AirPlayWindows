# AirPlay Sender pour Windows 11

**AirPlay Sender** est une application open-source pour Windows 11 permettant de capturer en temps réel l'ensemble du son système (WASAPI Loopback) et de le diffuser sans fil vers n'importe quel récepteur AirPlay ou RAOP (AirPlay 1 / AirPlay 2), avec un support éprouvé pour les enceintes haute fidélité telles que la **Devialet Phantom II**, les récepteurs Mac/Apple TV et les enceintes compatibles AirPlay.

---

## Fonctionnalités Principales

- **Capture universelle du son Windows** : capture transparente de tout ce qui est joué sur la sortie audio par défaut (navigateur, Spotify, YouTube, jeux, lecteurs vidéo).
- **Pipeline audio temps réel sans clics** :
  - Rééchantillonnage de haute qualité via `soxr` (48 kHz natif Windows vers 44.1 kHz PCM standard AirPlay).
  - Ring buffer circulaire borné (800 ms par défaut) avec politique de maintien du temps réel.
  - Générateur automatique de silence pour maintenir la connexion active même lors des pauses musicales.
- **Résilience et auto-réparation** :
  - Machine d'états explicite (`IDLE`, `DISCOVERING`, `CONNECTING`, `STREAMING`, `RECONNECTING`, `STOPPED`).
  - Reconnexion automatique avec backoff exponentiel et jitter aléatoire lors des micro-coupures réseau ou mise en veille de l'enceinte.
  - Bascule à chaud lors du changement de périphérique de sortie Windows (casque branché/débranché).
- **Interface discrète System Tray (Zone de notification)** :
  - Icône dynamique à 4 couleurs : gris (inactif), orange (connexion/reconnexion), vert (diffusion active), rouge (erreur).
  - Contrôle rapide du volume, sélection d'enceinte, démarrage avec Windows, ouverture des logs et copie du rapport de diagnostic.
  - Instance unique garantie via Mutex nommé Windows (`Local\AirPlaySender_SingleInstance_Mutex`).
- **Mode CLI complet** : sous-commandes `scan`, `pair`, `run`, `diag`, `tray`.
- **Packaging autonome** : exécutable autonome sans dépendance Python (`build.ps1`).

---

## Architecture du Projet

```
AirPlayWindows/
├── assets/                     # Icônes de l'application (.ico multi-résolution)
├── docs/
│   ├── DECISIONS.md            # Journal des décisions d'architecture (ADR)
│   ├── PROTOCOL_NOTES.md       # Analyse des paquets réseau et spécifications RAOP/AirPlay
│   ├── SPIKES.md               # Résultats des tests empiriques et benchmarks
│   └── TROUBLESHOOTING.md      # Arbre de décision et guide de dépannage
├── scripts/                    # Scripts utilitaires et prototypes de validation
│   ├── 01_scan.py              # Découverte réseau autonome
│   ├── 02_send_file.py         # Test de streaming fichier WAV vers récepteur
│   ├── 03_capture_to_wav.py    # Test de capture WASAPI loopback vers WAV
│   └── 04_live_stream.py       # Prototype de streaming direct temps réel
├── src/
│   └── airplay_sender/         # Code source du package
│       ├── __init__.py         # Version du package
│       ├── __main__.py         # Point d'entrée exécutable (python -m airplay_sender)
│       ├── audio_pipeline.py   # Tampon circulaire, rééchantillonneur soxr et silence
│       ├── autostart.py        # Gestion de la clé de registre Windows Run
│       ├── capture.py          # Capture WASAPI Loopback (PyAudioWPatch)
│       ├── cli.py              # Sous-commandes de la console (scan, pair, run, diag)
│       ├── config.py           # Modèles Pydantic v2 et persistance atomique
│       ├── devices_watch.py    # Surveillance du changement de sortie audio par défaut
│       ├── diagnostics.py      # Générateur du rapport de diagnostic
│       ├── gui.py              # Interface graphique moderne Tkinter & barre d'état
│       ├── icons.py            # Dessin vectoriel dynamique des icônes (Pillow)
│       ├── logging_setup.py    # Logs rotatifs et masquage des données sensibles
│       ├── metrics.py          # Compteurs de performance (underruns, overflows, jitter)
│       ├── single_instance.py  # Mutex nommé Win32 pour garantir l'instance unique
│       ├── streamer.py         # Adaptateur AudioSource pour pyatv / RAOP
│       ├── supervisor.py       # Machine d'états et auto-réparation
│       └── tray.py             # Interface pystray avec menu contextuel et toasts
├── tests/                      # Suite de tests automatisés (pytest)
├── build.ps1                   # Script de compilation autonome PyInstaller
├── pyproject.toml              # Configuration de build et d'outillage
└── requirements.txt            # Dépendances verrouillées
```

---

## Installation et Prérequis

### Prérequis Système
- Windows 10 (version 1903+) ou Windows 11.
- Python 3.10 à 3.13 (version 64-bit).
- Carte son avec pilote audio supportant WASAPI Loopback (standard sur 99% des PC Windows).
- Réseau local : le PC et le récepteur AirPlay doivent être sur le même sous-réseau (profil réseau Windows défini sur **Privé**).

### Installation dans un environnement virtuel

```powershell
# Cloner le dépôt ou se placer dans le dossier
cd c:\chemin\vers\AirPlayWindows

# Créer un environnement virtuel Python
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Installer les dépendances
pip install -r requirements.txt
```

### Sortie audio dédiée (optionnel) : VB-CABLE

Pour que seul le son voulu soit envoyé à l'enceinte (sans muter le PC), l'application peut utiliser un périphérique audio virtuel de type **VB-CABLE** (« CABLE Input »).

VB-CABLE est un logiciel tiers de [VB-Audio](https://vb-audio.com/Cable/) (gratuit, sous sa propre licence). Il n'est **pas** inclus dans ce dépôt et ne doit pas être redistribué : téléchargez-le depuis le site officiel.

1. Téléchargez et décompressez le pack VB-CABLE dans `tools\vbcable\` (le fichier `VBCABLE_Setup_x64.exe` doit s'y trouver).
2. Lancez `Installer_VB-CABLE.bat` (droits administrateur requis), puis cliquez sur **Install Driver**.

---

## Utilisation

### 1. Interface Graphique Fenêtrée (GUI) & Barre d'état — Recommandé

Double-cliquez simplement sur le raccourci **`AirPlaySender.lnk`** à la racine, ou exécutez :
```powershell
python -m airplay_sender
```
Une fenêtre moderne s'ouvre, accompagnée de son icône dans la zone de notification :
- **Badge et bouton principal** : visualisez en un clin d'œil l'état de la connexion et démarrez/arrêtez le streaming d'un clic.
- **Vu-mètre audio en direct** : observez en temps réel le niveau sonore capturé depuis Windows pour vérifier immédiatement que votre musique est bien prise en compte.
- **Enceinte cible** : sélectionnez votre enceinte parmi celles détectées ou actualisez la recherche mDNS.
- **Volume** : réglez le niveau de 0 à 100% avec le curseur ou les boutons d'accès rapide (15%, 30%, 50%, 75%).
- **Réduction intelligente** : la fermeture de la fenêtre [X] réduit l'application dans la zone de notification (System Tray) sans interrompre la musique. Un double-clic sur l'icône du Tray la fait réapparaître au premier plan.
- **Outils** : accès en un clic au dossier des logs et copie instantanée du rapport de diagnostic.

### 2. Utilisation en mode barre d'état uniquement (System Tray)

Pour démarrer directement réduit dans la barre d'état (idéal au démarrage de Windows) :
```powershell
python -m airplay_sender --minimized
# ou
python -m airplay_sender tray
```

### 3. Utilisation en ligne de commande (CLI)

```powershell
# Rechercher les enceintes disponibles et enregistrer la cible par défaut
python -m airplay_sender scan --save "Phantom"

# Lancer la diffusion directement en console
python -m airplay_sender run --volume 0.35

# Générer un rapport d'analyse de l'environnement (diagnostic)
python -m airplay_sender diag
```

---

## Configuration

Le fichier de configuration est enregistré automatiquement de manière atomique dans :
`%APPDATA%\airplay-sender\config.json`

Exemple de structure :
```json
{
  "device": {
    "name": "Phantom II 98 dB-1a2b",
    "identifier": "AA:BB:CC:DD:EE:FF",
    "last_known_ip": "192.168.1.42"
  },
  "credentials": {
    "raop": "",
    "airplay": ""
  },
  "audio": {
    "buffer_ms": 800,
    "target_samplerate": 44100,
    "capture_device": "default-loopback"
  },
  "volume": {
    "sync_with_windows": false,
    "default": 0.35
  },
  "behavior": {
    "autostart_stream": true,
    "start_minimized": true,
    "reconnect": true
  },
  "logging": {
    "level": "INFO"
  }
}
```

---

## Compilation de l'exécutable autonome (PyInstaller)

Pour générer un binaire autonome ne nécessitant aucune installation de Python :

```powershell
# Compilation standard sans console (mode silencieux pour l'utilisateur final)
.\build.ps1

# Ou compilation avec console de debug (pour le développement)
.\build.ps1 -DebugConsole
```

L'exécutable autonome se trouve dans `dist\AirPlaySender\AirPlaySender.exe`.

> [!WARNING]
> **Avertissement Antivirus** : Les binaires générés localement avec PyInstaller sans signature numérique payante (certificat Authenticode) peuvent occasionnellement être signalés comme faux positifs par Windows Defender ou d'autres antivirus. Si Windows Defender bloque le lancement, ajoutez le dossier `dist\AirPlaySender` dans les exclusions de sécurité.

---

## Logs et Diagnostic

- Les fichiers de logs sont enregistrés avec rotation automatique (5 Mo x 5 fichiers) dans :  
  `%LOCALAPPDATA%\airplay-sender\logs\app.log`
- Les informations sensibles (tokens d'authentification, credentials de pairing) sont automatiquement masquées (`***`).
- En cas de difficulté, lancez `python -m airplay_sender diag` ou cliquez sur "Copier le rapport de diagnostic" dans le menu System Tray.

---

## Limites Connues

- **Latence du protocole AirPlay** : En raison du protocole AirPlay/RAOP et du tampon de sécurité contre les micro-coupures réseau (800 ms à 1 s), il existe un décalage de transmission normal de 1 à 2 secondes. Cette latence est inhérente au protocole AirPlay 1 audio et convient parfaitement à l'écoute de musique, podcasts ou flux audio continus, mais n'est pas adaptée au gaming ultra-compétitif.
- **Protection DRM / Mode exclusif** : Si une application audio prend le contrôle exclusif de la carte son Windows (WASAPI Exclusive Mode), le loopback standard peut être temporairement indisponible jusqu'à la libération du périphérique.

---

## Licence

Ce projet est distribué sous licence [MIT](LICENSE).
