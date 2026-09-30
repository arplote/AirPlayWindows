"""Tests unitaires pour le mécanisme d'instance unique (single_instance.py)."""

from __future__ import annotations

import sys

from airplay_sender.single_instance import SingleInstance


def test_single_instance_acquisition() -> None:
    mutex_name = "Local\\Test_AirPlaySender_Mutex_12345"
    inst1 = SingleInstance(mutex_name=mutex_name)
    assert inst1.acquire() is True
    assert inst1.already_running is False

    if sys.platform == "win32":
        # Une deuxième tentative avec le même nom doit détecter le doublon
        inst2 = SingleInstance(mutex_name=mutex_name)
        assert inst2.acquire() is False
        assert inst2.already_running is True

    inst1.release()

    if sys.platform == "win32":
        # Après libération, une nouvelle acquisition doit réussir
        inst3 = SingleInstance(mutex_name=mutex_name)
        assert inst3.acquire() is True
        inst3.release()


def test_single_instance_context_manager() -> None:
    mutex_name = "Local\\Test_AirPlaySender_Context_12345"
    with SingleInstance(mutex_name=mutex_name) as inst:
        assert inst.already_running is False
