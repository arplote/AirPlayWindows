"""Choix de la sortie audio par défaut de Windows (équivalent du mixeur de son Windows).

S'appuie sur pycaw, qui encapsule l'interface COM non documentée IPolicyConfig utilisée
par les sélecteurs de sortie audio du marché (SoundSwitch, NirCmd...). Aucun droit
administrateur n'est requis. Chaque appel initialise COM pour le thread appelant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger("airplay_sender.audio_output")


@dataclass(frozen=True)
class OutputDevice:
    """Sortie audio Windows (périphérique de rendu actif)."""

    id: str
    name: str


class _ComContext:
    """Initialise COM pour le thread courant le temps d'un bloc."""

    def __enter__(self) -> None:
        import comtypes

        comtypes.CoInitialize()

    def __exit__(self, *args: object) -> None:
        import comtypes

        comtypes.CoUninitialize()


def list_output_devices() -> list[OutputDevice]:
    """Liste les sorties audio actives (haut-parleurs, casques, câbles virtuels...)."""
    from pycaw.pycaw import DEVICE_STATE, AudioUtilities, EDataFlow

    with _ComContext():
        devices = AudioUtilities.GetAllDevices(
            data_flow=EDataFlow.eRender.value, device_state=DEVICE_STATE.ACTIVE.value
        )
        return [OutputDevice(id=str(d.id), name=str(d.FriendlyName)) for d in devices]


def get_default_output() -> OutputDevice | None:
    """Sortie audio actuellement définie par défaut dans Windows, ou None."""
    from pycaw.pycaw import AudioUtilities

    try:
        with _ComContext():
            device: Any = AudioUtilities.GetSpeakers()
            return OutputDevice(id=str(device.id), name=str(device.FriendlyName))
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Sortie audio par défaut introuvable : %s", exc)
        return None


def set_default_output(device_id: str) -> None:
    """Définit la sortie par défaut pour les trois rôles Windows (console, média, communication).

    Lève OSError si Windows ou une politique de la machine refuse le changement.
    """
    from pycaw.pycaw import AudioUtilities, ERole

    with _ComContext():
        AudioUtilities.SetDefaultDevice(
            device_id, roles=[ERole.eConsole, ERole.eMultimedia, ERole.eCommunications]
        )
    _LOGGER.info("Sortie audio Windows définie sur l'identifiant %s", device_id)
