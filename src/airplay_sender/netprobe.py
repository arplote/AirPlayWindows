"""Sonde réseau : qualité des liens PC <-> box et PC <-> enceinte (diagnostic des coupures).

Mesure en continu, sans droits administrateur et sans lancer de processus externe
(netsh/ping peuvent être bloqués par la politique de la machine) :
- ping ICMP vers la passerelle (la box) et vers l'enceinte (iphlpapi.IcmpSendEcho) ;
- état de la liaison Wi-Fi du PC (wlanapi) : signal, débit, canal, point d'accès (BSSID).

Un ping vers l'enceinte traverse les deux tronçons si l'enceinte est aussi en Wi-Fi : une
dégradation simultanée des deux cibles désigne plutôt le tronçon PC <-> box, une enceinte
seule dégradée désigne plutôt box <-> enceinte (ou l'enceinte elle-même).
"""

from __future__ import annotations

import ctypes
import logging
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger("airplay_sender.netprobe")

_is_windows = sys.platform == "win32"

# Une fenêtre (30 s) est jugée dégradée à partir de ces seuils
DEGRADED_LOSS_RATIO = 0.01
DEGRADED_P99_MS = 100.0
SLOW_PING_MS = 100.0
CONSECUTIVE_LOSS_WARN = 3


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * pct))]


@dataclass
class PingWindow:
    """Résultat d'une fenêtre de mesure pour une cible."""

    sent: int
    lost: int
    p50_ms: float
    p99_ms: float
    max_ms: float
    slow: int

    @property
    def loss_ratio(self) -> float:
        return self.lost / self.sent if self.sent else 0.0

    @property
    def degraded(self) -> bool:
        return self.sent > 0 and (
            self.loss_ratio >= DEGRADED_LOSS_RATIO or self.p99_ms >= DEGRADED_P99_MS
        )

    def format(self) -> str:
        if not self.sent:
            return "aucune mesure"
        return (
            f"perte {self.loss_ratio * 100:.1f}% ({self.lost}/{self.sent}) "
            f"p50={self.p50_ms:.0f}ms p99={self.p99_ms:.0f}ms max={self.max_ms:.0f}ms "
            f">{SLOW_PING_MS:.0f}ms:{self.slow}"
        )


class PingStats:
    """Cumule les résultats de ping d'une cible (thread-safe)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._rtts: list[float] = []
        self._lost = 0
        self._consecutive_lost = 0
        self._outage_start = 0.0
        self.total_sent = 0
        self.total_lost = 0

    def record(self, rtt_ms: float | None, now: float | None = None) -> None:
        """Enregistre un ping réussi (rtt_ms) ou perdu (None)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self.total_sent += 1
            if rtt_ms is None:
                self._lost += 1
                self.total_lost += 1
                self._consecutive_lost += 1
                if self._consecutive_lost == 1:
                    self._outage_start = now
                if self._consecutive_lost == CONSECUTIVE_LOSS_WARN:
                    _LOGGER.warning(
                        "[RÉSEAU] %s : %d pings perdus d'affilée", self.name, CONSECUTIVE_LOSS_WARN
                    )
                return
            if self._consecutive_lost >= CONSECUTIVE_LOSS_WARN:
                _LOGGER.warning(
                    "[RÉSEAU] %s : de nouveau joignable après %d pings perdus (~%.1f s)",
                    self.name,
                    self._consecutive_lost,
                    now - self._outage_start,
                )
            self._consecutive_lost = 0
            self._rtts.append(rtt_ms)

    def snapshot_and_reset(self) -> PingWindow:
        with self._lock:
            rtts, lost = self._rtts, self._lost
            self._rtts, self._lost = [], 0
        ordered = sorted(rtts)
        return PingWindow(
            sent=len(rtts) + lost,
            lost=lost,
            p50_ms=_percentile(ordered, 0.5),
            p99_ms=_percentile(ordered, 0.99),
            max_ms=ordered[-1] if ordered else 0.0,
            slow=sum(1 for r in rtts if r > SLOW_PING_MS),
        )


class IcmpPinger:
    """Ping ICMP via iphlpapi (sans droits admin, sans processus externe)."""

    _PAYLOAD = b"airplay-sender-probe-0123456789"

    def __init__(self) -> None:
        self._dll: Any = None
        self._handle: Any = None
        if not _is_windows:
            return
        try:
            self._dll = ctypes.WinDLL("iphlpapi.dll")
            self._dll.IcmpCreateFile.restype = ctypes.c_void_p
            self._dll.IcmpCloseHandle.argtypes = [ctypes.c_void_p]
            self._dll.IcmpSendEcho.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_char_p,
                ctypes.c_ushort,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_ulong,
            ]
            self._dll.IcmpSendEcho.restype = ctypes.c_ulong
            self._handle = self._dll.IcmpCreateFile()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("ICMP indisponible : %s", exc)
            self._handle = None

    @property
    def available(self) -> bool:
        return bool(self._handle)

    def ping(self, ip: str, timeout_ms: int) -> float | None:
        """Renvoie le temps aller-retour en ms, ou None si perdu / indisponible."""
        if not self._handle:
            return None
        try:
            dest = struct.unpack("<L", socket.inet_aton(ip))[0]
            reply = ctypes.create_string_buffer(128)
            t0 = time.perf_counter()
            count = self._dll.IcmpSendEcho(
                self._handle, dest, self._PAYLOAD, len(self._PAYLOAD), None, reply, 128, timeout_ms
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if count == 0:
                return None
            status = struct.unpack_from("<L", reply, 4)[0]  # ICMP_ECHO_REPLY.Status
            return elapsed_ms if status == 0 else None
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Erreur ping %s : %s", ip, exc)
            return None

    def close(self) -> None:
        if self._handle and self._dll:
            try:
                self._dll.IcmpCloseHandle(self._handle)
            except Exception:  # noqa: BLE001
                pass
        self._handle = None


class _MibIpForwardRow(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulong)
        for name in (
            "dwForwardDest",
            "dwForwardMask",
            "dwForwardPolicy",
            "dwForwardNextHop",
            "dwForwardIfIndex",
            "dwForwardType",
            "dwForwardProto",
            "dwForwardAge",
            "dwForwardNextHopAS",
            "dwForwardMetric1",
            "dwForwardMetric2",
            "dwForwardMetric3",
            "dwForwardMetric4",
            "dwForwardMetric5",
        )
    ]


def find_default_gateway() -> str | None:
    """Adresse IPv4 de la passerelle par défaut (la box), ou None."""
    if not _is_windows:
        return None
    try:
        iphlpapi = ctypes.WinDLL("iphlpapi.dll")
        probe_dest = struct.unpack("<L", socket.inet_aton("8.8.8.8"))[0]
        row = _MibIpForwardRow()
        if iphlpapi.GetBestRoute(probe_dest, 0, ctypes.byref(row)) != 0:
            return None
        if row.dwForwardNextHop in (0, probe_dest):
            return None
        return socket.inet_ntoa(struct.pack("<L", row.dwForwardNextHop))
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Passerelle introuvable : %s", exc)
        return None


# --- Liaison Wi-Fi du PC (wlanapi) -----------------------------------------------------
# Windows 11 refuse l'attribut « connexion courante » (BSSID, débit) sans autorisation de
# localisation ; le RSSI, le canal et les compteurs radio restent lisibles et suffisent.


class _WlanInterfaceInfo(ctypes.Structure):
    _fields_ = [
        ("InterfaceGuid", ctypes.c_ubyte * 16),
        ("strInterfaceDescription", ctypes.c_wchar * 256),
        ("isState", ctypes.c_int),
    ]


_WLAN_OPCODE_CHANNEL_NUMBER = 8
_WLAN_OPCODE_STATISTICS = 0x10000101
_WLAN_OPCODE_RSSI = 0x10000102
_WLAN_STATE_CONNECTED = 1
# WLAN_STATISTICS : 3 ULONGLONG d'en-tête, 2 x WLAN_MAC_FRAME_STATISTICS (12 ULONGLONG),
# dwNumberOfPhys (ULONG + remplissage) puis WLAN_PHY_FRAME_STATISTICS (16 ULONGLONG) par PHY.
_STATS_PHY_OFFSET_ULL = 28
_PHY_TX, _PHY_FAILED, _PHY_RETRY, _PHY_MULTI_RETRY = 0, 2, 3, 4

RETRY_DEGRADED_RATIO = 0.10


@dataclass
class WifiSample:
    """Instantané de la liaison Wi-Fi du PC (compteurs radio cumulés depuis la connexion)."""

    rssi_dbm: int
    channel: int | None
    tx_frames: int
    retries: int
    multi_retries: int
    failed: int

    @property
    def band(self) -> str:
        if self.channel is None:
            return "?"
        return "2.4 GHz" if self.channel <= 14 else "5/6 GHz"


def query_wifi() -> WifiSample | None:
    """Lit l'état de la liaison Wi-Fi courante, ou None (Ethernet, pas de Wi-Fi, erreur)."""
    if not _is_windows:
        return None
    try:
        wlan = ctypes.WinDLL("wlanapi.dll")
    except OSError:
        return None

    client = ctypes.c_void_p()
    negotiated = ctypes.c_ulong()
    if wlan.WlanOpenHandle(2, None, ctypes.byref(negotiated), ctypes.byref(client)) != 0:
        return None

    def query(guid: Any, opcode: int) -> tuple[Any, int] | None:
        """Renvoie (octets copiés, taille) de la réponse, ou None en cas d'erreur."""
        size = ctypes.c_ulong()
        data = ctypes.c_void_p()
        value_type = ctypes.c_int()
        err = wlan.WlanQueryInterface(
            client,
            guid,
            opcode,
            None,
            ctypes.byref(size),
            ctypes.byref(data),
            ctypes.byref(value_type),
        )
        if err != 0 or not data.value:
            return None
        try:
            return ctypes.string_at(data.value, size.value), int(size.value)
        finally:
            wlan.WlanFreeMemory(data)

    try:
        iface_list = ctypes.c_void_p()
        if wlan.WlanEnumInterfaces(client, None, ctypes.byref(iface_list)) != 0:
            return None
        try:
            list_addr = int(iface_list.value or 0)
            count = ctypes.c_ulong.from_address(list_addr).value
            base = list_addr + 8  # dwNumberOfItems + dwIndex
            for i in range(count):
                info = _WlanInterfaceInfo.from_address(base + i * ctypes.sizeof(_WlanInterfaceInfo))
                if info.isState != _WLAN_STATE_CONNECTED:
                    continue
                guid = ctypes.byref(info, 0)

                rssi_raw = query(guid, _WLAN_OPCODE_RSSI)
                if rssi_raw is None:
                    continue
                channel_raw = query(guid, _WLAN_OPCODE_CHANNEL_NUMBER)
                stats_raw = query(guid, _WLAN_OPCODE_STATISTICS)
                return _build_sample(rssi_raw[0], channel_raw[0] if channel_raw else None,
                                     stats_raw[0] if stats_raw else None)
            return None
        finally:
            wlan.WlanFreeMemory(iface_list)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Lecture Wi-Fi impossible : %s", exc)
        return None
    finally:
        wlan.WlanCloseHandle(client, None)


def _build_sample(rssi: bytes, channel: bytes | None, stats: bytes | None) -> WifiSample:
    """Assemble un WifiSample depuis les réponses brutes de WlanQueryInterface."""
    tx = failed = retries = multi = 0
    if stats is not None and len(stats) >= (_STATS_PHY_OFFSET_ULL + 5) * 8:
        ulls = struct.unpack_from(f"<{len(stats) // 8}Q", stats)
        phy = ulls[_STATS_PHY_OFFSET_ULL:]
        tx, failed, retries, multi = (
            phy[_PHY_TX],
            phy[_PHY_FAILED],
            phy[_PHY_RETRY],
            phy[_PHY_MULTI_RETRY],
        )
    return WifiSample(
        rssi_dbm=struct.unpack_from("<l", rssi)[0],
        channel=struct.unpack_from("<L", channel)[0] if channel else None,
        tx_frames=tx,
        retries=retries,
        multi_retries=multi,
        failed=failed,
    )


@dataclass
class WifiWindow:
    """Résumé radio d'une fenêtre de mesure."""

    rssi_min: int
    rssi_avg: float
    channel: int | None
    band: str
    tx: int
    retries: int
    multi_retries: int
    failed: int
    channel_changes: int

    @property
    def retry_ratio(self) -> float:
        return self.retries / self.tx if self.tx else 0.0

    @property
    def degraded(self) -> bool:
        return self.tx > 0 and (self.retry_ratio >= RETRY_DEGRADED_RATIO or self.failed > 0)

    def format(self) -> str:
        return (
            f"Wi-Fi: RSSI min {self.rssi_min} dBm (moy {self.rssi_avg:.0f}) "
            f"canal {self.channel} ({self.band}) | "
            f"retries {self.retry_ratio * 100:.1f}% ({self.retries}/{self.tx} trames, "
            f"{self.multi_retries} multiples) échecs: {self.failed} | "
            f"changements de canal: {self.channel_changes}"
        )


class WifiStats:
    """Résume les instantanés Wi-Fi d'une fenêtre (deltas de compteurs, changements de canal)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: list[WifiSample] = []
        self._baseline: WifiSample | None = None
        self._last_channel: int | None = None
        self._channel_changes = 0
        self.total_channel_changes = 0
        self.total_tx = 0
        self.total_retries = 0
        self.worst_rssi: int | None = None
        self.last_band = "?"
        self.last_channel: int | None = None

    def record(self, sample: WifiSample | None) -> None:
        if sample is None:
            return
        with self._lock:
            self._samples.append(sample)
            if self._baseline is None:
                self._baseline = sample
            if self._last_channel is not None and sample.channel != self._last_channel:
                self._channel_changes += 1
                self.total_channel_changes += 1
                _LOGGER.warning(
                    "[RÉSEAU] Wi-Fi : changement de canal %s -> %s (RSSI %d dBm)",
                    self._last_channel,
                    sample.channel,
                    sample.rssi_dbm,
                )
            self._last_channel = sample.channel
            self.last_channel, self.last_band = sample.channel, sample.band
            if self.worst_rssi is None or sample.rssi_dbm < self.worst_rssi:
                self.worst_rssi = sample.rssi_dbm

    def snapshot_and_reset(self) -> WifiWindow | None:
        with self._lock:
            samples, self._samples = self._samples, []
            changes, self._channel_changes = self._channel_changes, 0
            base = self._baseline
            if not samples or base is None:
                return None
            last = samples[-1]
            self._baseline = last

        def delta(now: int, before: int) -> int:
            return max(0, now - before)  # compteur remis à zéro (reconnexion) : ignoré

        window = WifiWindow(
            rssi_min=min(s.rssi_dbm for s in samples),
            rssi_avg=sum(s.rssi_dbm for s in samples) / len(samples),
            channel=last.channel,
            band=last.band,
            tx=delta(last.tx_frames, base.tx_frames),
            retries=delta(last.retries, base.retries),
            multi_retries=delta(last.multi_retries, base.multi_retries),
            failed=delta(last.failed, base.failed),
            channel_changes=changes,
        )
        self.total_tx += window.tx
        self.total_retries += window.retries
        return window


# --- Orchestrateur -----------------------------------------------------------------------


class NetworkProbe:
    """Lance les sondes (box, enceinte, Wi-Fi) et produit les résumés périodiques."""

    BOX_INTERVAL_SEC = 1.0
    SPEAKER_INTERVAL_SEC = 0.25
    WIFI_INTERVAL_SEC = 5.0
    PING_TIMEOUT_MS = 500

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._speaker_ip: str | None = None
        self._gateway: str | None = None
        self._box = PingStats("Box")
        self._speaker = PingStats("Enceinte")
        self._wifi = WifiStats()
        self._windows = 0
        self._box_degraded = 0
        self._speaker_degraded = 0
        self._both_degraded = 0
        self._wifi_degraded = 0

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def start(self, speaker_ip: str) -> None:
        """Démarre les sondes ; sans effet si déjà actives pour cette adresse."""
        if self.running and speaker_ip == self._speaker_ip:
            return
        self.stop()
        self._stop.clear()
        self._speaker_ip = speaker_ip
        self._gateway = find_default_gateway()
        _LOGGER.info(
            "Sonde réseau : box=%s enceinte=%s", self._gateway or "introuvable", speaker_ip
        )

        if self._gateway:
            self._spawn(
                "NetProbe-box", self._ping_loop, self._gateway, self._box, self.BOX_INTERVAL_SEC
            )
        self._spawn(
            "NetProbe-speaker",
            self._ping_loop,
            speaker_ip,
            self._speaker,
            self.SPEAKER_INTERVAL_SEC,
        )
        self._spawn("NetProbe-wifi", self._wifi_loop)

    def _spawn(self, name: str, target: Any, *args: Any) -> None:
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1.5)
        self._threads = []

    def _ping_loop(self, ip: str, stats: PingStats, interval: float) -> None:
        pinger = IcmpPinger()
        if not pinger.available:
            _LOGGER.debug("Sonde ICMP indisponible pour %s.", ip)
            return
        try:
            next_t = time.perf_counter()
            while not self._stop.is_set():
                stats.record(pinger.ping(ip, self.PING_TIMEOUT_MS))
                next_t += interval
                delay = next_t - time.perf_counter()
                if delay < 0:
                    next_t, delay = time.perf_counter(), 0.0
                if self._stop.wait(delay):
                    break
        finally:
            pinger.close()

    def _wifi_loop(self) -> None:
        while not self._stop.is_set():
            self._wifi.record(query_wifi())
            if self._stop.wait(self.WIFI_INTERVAL_SEC):
                break

    def snapshot_and_reset(self) -> str:
        """Résumé de la fenêtre écoulée (ligne [RÉSEAU]) ; vide si la sonde est inactive."""
        if not self.running:
            return ""
        box = self._box.snapshot_and_reset()
        speaker = self._speaker.snapshot_and_reset()
        self._windows += 1
        self._box_degraded += box.degraded
        self._speaker_degraded += speaker.degraded
        self._both_degraded += box.degraded and speaker.degraded
        wifi = self._wifi.snapshot_and_reset()
        wifi_degraded = wifi is not None and wifi.degraded
        self._wifi_degraded += wifi_degraded
        flag = " !!" if box.degraded or speaker.degraded or wifi_degraded else ""
        wifi_txt = wifi.format() if wifi else "Wi-Fi: indisponible (Ethernet ?)"
        return (
            f"[RÉSEAU]{flag} Box({self._gateway}): {box.format()} | "
            f"Enceinte({self._speaker_ip}): {speaker.format()} | {wifi_txt}"
        )

    def session_report(self) -> str:
        """Bilan de session : combien de fenêtres dégradées par tronçon, et lecture probable."""
        if not self._windows:
            return ""
        a, b, c = self._box_degraded, self._speaker_degraded, self._both_degraded
        if a == 0 and b == 0 and self._wifi_degraded == 0:
            reading = (
                "aucune dégradation réseau mesurée : "
                "le problème n'est pas (ou pas seulement) le réseau"
            )
        elif a == 0 and b == 0:
            reading = (
                "pings sains mais forte proportion de trames Wi-Fi retransmises : "
                "liaison radio du PC médiocre (interférences/saturation), à surveiller"
            )
        elif c >= 0.7 * max(a, b):
            reading = (
                "dégradations simultanées box + enceinte : "
                "plutôt le tronçon PC <-> box (Wi-Fi du PC)"
            )
        elif b > a:
            reading = (
                "enceinte seule dégradée : plutôt le tronçon box <-> enceinte, "
                "ou l'enceinte elle-même"
            )
        else:
            reading = (
                "box dégradée sans l'enceinte : situation atypique, voir les détails par fenêtre"
            )
        wifi = self._wifi
        retry_pct = wifi.total_retries / wifi.total_tx * 100 if wifi.total_tx else 0.0
        if wifi.last_band == "2.4 GHz":
            reading += " | PC connecté en 2.4 GHz (bande souvent saturée) : essayer le 5 GHz"
        return (
            f"[RÉSEAU] Bilan de session : {self._windows} fenêtres de 30 s | "
            f"box dégradée: {a} | enceinte dégradée: {b} | les deux: {c} | "
            f"Wi-Fi radio dégradé: {self._wifi_degraded} | "
            f"pings perdus box {self._box.total_lost}/{self._box.total_sent}, "
            f"enceinte {self._speaker.total_lost}/{self._speaker.total_sent} | "
            f"retries Wi-Fi {retry_pct:.1f}% | pire RSSI {wifi.worst_rssi} dBm | "
            f"canal {wifi.last_channel} ({wifi.last_band}), changements: "
            f"{wifi.total_channel_changes} | Lecture: {reading}"
        )
