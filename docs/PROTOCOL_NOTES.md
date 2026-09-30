# Notes de Protocole (AirPlay / RAOP / mDNS)

Ce document consigne les informations réseau, TXT records des équipements découverts et observations de trafic.

---

## Scan initial (Phase 1 — 2026-09-30)

### 1. Appareil cible détecté
- **Nom mDNS** : `Phantom II 98 dB-1a2b`
- **Fabricant** : Devialet
- **Modèle** : Phantom II 98 dB
- **Numéro de série** : `Q49X00550TQ10`
- **Adresse IPv4** : `192.168.1.42`
- **Adresse MAC** : `AA:BB:CC:DD:EE:FF`
- **Port de service** : `7000` (AirPlay & RAOP)
- **Version firmware (`fv`)** : `p20.2.18.1` (srcvers: `366.0`, protovers: `1.1`)

---

### 2. Services annoncés et TXT Records

#### Service `_airplay._tcp.local.`
- **Nom** : `Phantom II 98 dB-1a2b._airplay._tcp.local.`
- **Port** : `7000`
- **Appairage requis** : `NotNeeded`
- **Mot de passe (`pw`)** : Aucun (`requires_password=False`)
- **Clé publique (`pk`)** : `a0e3020c124df245a55b06b4d466edf60a34b638f8ab550803b882d785b5f927`
- **deviceid / pi** : `AA:BB:CC:DD:EE:FF`
- **Features (`features` = `0x445F8A00,0x1C340`)** :
  - Bit 9 : MetaData
  - Bit 11 : AudioStream
  - Bit 15 : AAC_LC
  - Bit 16 : AAC_ELD
  - Bit 17 : MultiChannel
  - Bit 18 : RSA_AUTH
  - Bit 19 : Pairing
  - Bit 20 : BufferedAudio
  - Bit 22 : ScreenMultiCodec
  - Bit 26 : CoreUtilsPairing
  - Bit 30 : MFiHAP
  - Bit 38 : TransientPairing
  - Bit 40 : MultiRoom
  - Bit 46 : AudioUncompressed
  - Bit 48 : AudioClock

#### Service `_raop._tcp.local.`
- **Nom** : `AABBCCDDEEFF@Phantom II 98 dB-1a2b._raop._tcp.local.`
- **Port** : `7000`
- **Transport (`tp`)** : `UDP`
- **Codecs (`cn`)** : `0,1`
  - `0` : PCM (16 bits, 44.1 kHz, stéréo)
  - `1` : ALAC (Apple Lossless Audio Codec)
- **Chiffrement (`et`)** : `0,4`
  - `0` : None / Clear (chiffrement non obligatoire, flux audio direct supporté)
  - `4` : FairPlay v2
- **Flags système (`sf`)** : `0x4`
- **Metadata (`md`)** : `0,1,2` (Text, Artwork, Progress)

---

### 3. Sortie brute JSON du scan initial

```json
{
  "pyatv_devices": [
    {
      "name": "Phantom II 98 dB-1a2b",
      "address": "192.168.1.42",
      "identifier": "AA:BB:CC:DD:EE:FF",
      "all_identifiers": [
        "AA:BB:CC:DD:EE:FF",
        "AABBCCDDEEFF"
      ],
      "device_info": {
        "model": "DeviceModel.Unknown",
        "model_str": "Phantom II 98 dB",
        "operating_system": "OperatingSystem.Unknown",
        "version": null,
        "mac": "AA:BB:CC:DD:EE:FF",
        "raw_model": "Phantom II 98 dB"
      },
      "services": [
        {
          "protocol": "AirPlay",
          "port": 7000,
          "pairing_requirement": "PairingRequirement.NotNeeded",
          "requires_password": false,
          "properties_raw": {
            "acl": "0",
            "deviceid": "AA:BB:CC:DD:EE:FF",
            "features": "0x445F8A00,0x1C340",
            "rsf": "0x0",
            "fv": "p20.2.18.1",
            "flags": "0x4",
            "model": "Phantom II 98 dB",
            "manufacturer": "Devialet",
            "serialnumber": "Q49X00550TQ10",
            "protovers": "1.1",
            "srcvers": "366.0",
            "pi": "AA:BB:CC:DD:EE:FF",
            "gid": "AA:BB:CC:DD:EE:FF",
            "gcgl": "0",
            "pk": "a0e3020c124df245a55b06b4d466edf60a34b638f8ab550803b882d785b5f927"
          }
        },
        {
          "protocol": "RAOP",
          "port": 7000,
          "pairing_requirement": "PairingRequirement.NotNeeded",
          "requires_password": false,
          "properties_raw": {
            "cn": "0,1",
            "da": "true",
            "et": "0,4",
            "ft": "0x445F8A00,0x1C340",
            "fv": "p20.2.18.1",
            "md": "0,1,2",
            "am": "Phantom II 98 dB",
            "sf": "0x4",
            "tp": "UDP",
            "vn": "65537",
            "vs": "366.0",
            "pk": "a0e3020c124df245a55b06b4d466edf60a34b638f8ab550803b882d785b5f927"
          }
        }
      ]
    }
  ]
}
```
