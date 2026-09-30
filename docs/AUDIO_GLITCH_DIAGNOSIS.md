# Diagnostic : hachures audio AirPlay depuis Windows (absentes depuis iPhone)

## Contexte

Le son caste vers une enceinte Devialet en AirPlay depuis ce projet Python
(Windows) sature parfois par des saccades/hachures, qui n'arrivent jamais en
castant depuis un iPhone vers la même enceinte.

Le commit `6bb0db8` avait réduit la latence bout-en-bout de ~3000ms à ~330ms
("low-latency RAOP and backlog purge"), ce qui est le point de départ le plus
probable du problème : moins de latence = moins de marge pour absorber les
irrégularités.

## Architecture observée

- App Python utilisant `pyatv` (librairie mature RAOP/AirTunes) pour le protocole
  RTP/RTSP — ce n'est pas une stack RTP maison.
- Capture via `pyaudiowpatch` (wrapper WASAPI loopback / PortAudio).
- Pipeline : capture → buffer/ring buffer maison (`audio_pipeline.py`) → pyatv
  `stream_client` pour le RTP → Devialet.
- GUI Tkinter (VU-mètre) sur le thread principal, capture sur un thread dédié,
  boucle asyncio (pacing RTP) sur un autre thread.

## Hypothèses classées par probabilité

**1. Marge de buffer devenue trop fine (hypothèse principale)**
`audio_pipeline.py` : `buffer_ms` par défaut réduit de 800→80ms,
`max_buffer_bytes` réduit de 2.0x→1.5x la cible. Il ne restait qu'environ 40ms
de marge avant que le ring buffer ne purge (le "backlog purge" du commit
`6bb0db8`). `streamer.py` force aussi la latence RAOP négociée à 250ms (contre
~1500ms avant). Tout ralentissement de capture/resampling/scheduling Python
plus grand que ~40ms produit soit un **underrun → silence audible**, soit un
**overflow → drop**. L'iPhone, lui, tourne une stack audio temps réel native
avec des buffers bien plus généreux, donc n'est jamais exposé à ce cas limite.

**2. Résolution du timer Windows non ajustée + pacing RTP basé sur `asyncio.sleep`**
Le pacing des paquets RTP est géré dans pyatv : `stream_client.py` calcule un
`diff` (~8ms par paquet de 352 frames à 44.1kHz) et fait `await
asyncio.sleep(diff)`. Sous Windows, la granularité par défaut du timer est
~15.6ms tant que `timeBeginPeriod(1)` n'est pas appelé. Un sleep de 8ms peut
donc facilement déraper de 8-15ms, déclenchant un rattrapage qui envoie une
rafale de paquets — une source de gigue typique d'un sender "amateur" par
rapport au scheduler haute résolution natif d'iOS.

**3. Pas de priorité thread temps réel, contention GIL entre 3 threads**
Thread de capture sans élévation de priorité ; boucle asyncio (envoi/pacing
RTP) sur un second thread ; Tkinter (VU-mètre) sur le thread principal. Aucun
mécanisme de priorité temps réel. La contention GIL entre resampling
numpy/soxr, redraw GUI et boucle de pacing asyncio peut retarder l'envoi de
paquets précisément quand le buffer n'a que 80-120ms de profondeur.

**4. Pas de synchronisation NTP/adaptation de gigue, PCM brut (pas d'ALAC)**
Pas d'ajustement adaptatif du buffer selon la gigue réseau observée (buffer
cible statique avant ce travail). PCM brut envoyé (pas d'ALAC/AAC) : suffisant
en bande passante LAN mais aucune résilience aux erreurs — un paquet UDP
perdu/retardé est audible immédiatement.

## Plan d'action priorisé et statut

### Tier 0 — à vérifier manuellement, coût ~zéro, pas de code (non automatisable)
- **Gestion d'énergie Wi-Fi Windows** : désactiver "Allow the computer to turn
  off this device to save power" sur l'adaptateur Wi-Fi (Gestionnaire de
  périphériques → carte réseau → Alimentation) + désactiver le mode économie
  d'énergie de la carte. Cause très fréquente de micro-rafales sur PC, absente
  sur iPhone. **À tester en premier.**
- Tester en Ethernet le temps d'un essai pour isoler "réseau" vs "pipeline
  logiciel".

### Tier 1 — implémenté ✅
- `max_buffer_bytes` remonté de 1.5x à 2.0x la cible du ring buffer
  (`audio_pipeline.py`) pour réduire la fréquence des purges.
- `timeBeginPeriod(1)` appelé au démarrage du process (`__main__.py`),
  `timeEndPeriod(1)` à la fermeture, pour fixer la granularité du timer
  Windows à 1ms au lieu de ~15.6ms.
- Défaut de latence relevé de 80ms/250ms (~330ms total) à 100ms/350ms
  (~450ms total) — correspond au profil "Équilibré" déjà présent dans la GUI
  (`config.py`, migration legacy mise à jour en conséquence).

### Tier 2 — implémenté ✅
- **Latence adaptative en temps réel (AIMD)** (`supervisor.py:
  _adaptive_latency_loop`) : ajuste `buffer_ms` par paliers de +30ms en cas
  d'incident (underrun/overflow détecté sur une fenêtre de 5s), redescend par
  paliers de -15ms vers la valeur de base en l'absence d'incident, plafonné à
  base+250ms (max 400ms). Ne touche jamais à la latence RAOP matérielle.
- **MMCSS** (`mmcss.py`, `AvSetMmThreadCharacteristics("Pro Audio")`) appliqué
  au thread de capture (`supervisor.py: _capture_feeder_worker`) et au thread
  de la boucle asyncio qui porte le pacing RTP (`gui.py` et `tray.py:
  _run_asyncio_loop`) — priorité d'ordonnancement temps réel gérée et
  throttlée par l'OS, sans risque de geler le système contrairement à un
  `SetThreadPriority` brut.

### Tier 3 — non implémenté, à explorer seulement si le problème persiste
- Antivirus/pare-feu inspectant chaque paquet UDP — à tester en désactivant
  temporairement pour comparer.
- `SetThreadPriority(THREAD_PRIORITY_TIME_CRITICAL)` brut si MMCSS s'avère
  insuffisant — risque de figer le reste de Windows si mal maîtrisé, dernier
  recours seulement.
- **Extraire la boucle de cadencement RTP dans une extension native** (C via
  `ctypes`, ou Rust via `PyO3`/`maturin`) pour échapper au GIL/GC Python sur ce
  point précis, si mesuré nécessaire. Plus ciblé et moins risqué qu'un driver
  kernel (voir note ci-dessous), mais demande un toolchain de compilation et
  une interface Python↔natif — le plus gros effort de la liste.
- Réévaluer une fois le travail sur la sortie audio dédiée (câble virtuel)
  terminé — le comportement côté capture WASAPI (device partagé vs exclusif)
  peut changer la donne avant même de toucher au reste.

### Note : userland vs kernel-mode (écarté)
Écrire un driver kernel-mode (type WDM/KS) pour déporter le cadencement a été
envisagé mais écarté : sur Windows 10/11 un driver kernel doit être signé
(certificat EV + attestation Microsoft), un bug peut causer un BSOD (contre un
simple plantage d'app en userland), et ce niveau d'effort est réservé aux vrais
pilotes audio (ASIO, etc.) — disproportionné ici. **MMCSS répond déjà au même
besoin sans ce risque** : c'est une API userland qui s'appuie sur un service
kernel déjà présent dans Windows (`mmcss.sys`), sans écrire de driver ni
nécessiter les droits admin. Le vrai goulot suspecté n'est pas "userland vs
kernel" mais "Python/GIL vs code compilé" pour la boucle de cadencement précise
— d'où la piste Tier 3 ci-dessus, à activer seulement si mesurée nécessaire.

## Vérification effectuée

- Suite de tests complète (`pytest`, 42 tests) : verte après implémentation.
- Un bug a été détecté et corrigé pendant l'implémentation : la première
  version de la boucle AIMD utilisait le `sleep_func` injectable du
  superviseur (prévu pour accélérer les tests de reconnexion) au lieu d'un
  vrai `asyncio.sleep`. Avec un `AsyncMock` qui résout instantanément sans
  jamais suspendre réellement la coroutine, la boucle tournait en livelock et
  gelait toute la boucle asyncio (et donc les tests). Corrigé en utilisant
  `asyncio.sleep(window_sec)` directement pour la cadence de cette boucle.

## Reste à faire

- Vérifier manuellement le Tier 0 (réglages d'énergie Wi-Fi, test en Ethernet)
  — hors du périmètre du code.
- Réécouter en conditions réelles pour confirmer l'amélioration ; ajuster les
  constantes de l'AIMD (paliers, plafond) si besoin après retour d'usage.
- Tier 3 seulement si les hachures persistent après tout ce qui précède.
