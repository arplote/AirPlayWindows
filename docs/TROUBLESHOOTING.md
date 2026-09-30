# Guide de Dépannage (Troubleshooting)

## Problèmes fréquents et résolutions

### 1. Découverte (mDNS / Zeroconf)
- **Symptôme** : Aucun appareil découvert par le scan.
- **Vérifications** :
  - Profil réseau Windows : doit être en profil "Privé" (vérifier avec `Get-NetConnectionProfile`).
  - Pare-feu Windows : autoriser le trafic UDP 5353 entrant.
  - Interfaces virtuelles : si des interfaces VPN, WSL ou Hyper-V sont actives, mDNS peut partir sur la mauvaise interface.

### 2. Capture Audio (WASAPI Loopback)
- **Symptôme** : Silence permanent ou exception à l'ouverture du stream.
- **Vérifications** :
  - Vérifier qu'une sortie audio par défaut est active.
  - S'assurer qu'aucune application n'a pris le contrôle exclusif de la carte son.
