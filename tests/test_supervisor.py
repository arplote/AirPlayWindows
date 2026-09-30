"""Tests unitaires pour la machine d'états du superviseur (supervisor.py)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from airplay_sender.supervisor import State, Supervisor
from tests.fakes import FakeDeviceConfig, FakeStreamer


def _create_mock_capture() -> MagicMock:
    mock_cap = MagicMock()
    mock_cap.samplerate = 48000
    mock_cap.channels = 2
    mock_cap.read.return_value = None
    return mock_cap


@pytest.mark.asyncio
async def test_supervisor_state_transitions_success() -> None:
    """Vérifie la séquence normale des états.

    IDLE -> DISCOVERING -> CONNECTING -> STREAMING -> STOPPED.
    """
    states_visited: list[State] = []
    fake_streamer = FakeStreamer()
    fake_device = FakeDeviceConfig(name="Devialet Test")

    sleep_mock = AsyncMock()

    supervisor = Supervisor(
        target_name="Devialet Test",
        on_state_change=states_visited.append,
        sleep_func=sleep_mock,
    )
    supervisor._streamer = fake_streamer  # type: ignore[assignment]

    with (
        patch("airplay_sender.supervisor.pyatv.scan", new=AsyncMock(return_value=[fake_device])),
        patch("airplay_sender.supervisor.LoopbackCapture", return_value=_create_mock_capture()),
        patch("airplay_sender.supervisor.AudioDeviceWatcher"),
    ):
        run_task = asyncio.create_task(supervisor.run())

        # Laisser le temps à la boucle d'entrer en STREAMING
        await asyncio.sleep(0.05)
        assert supervisor.state == State.STREAMING
        assert State.DISCOVERING in states_visited
        assert State.CONNECTING in states_visited
        assert State.STREAMING in states_visited

        # Demande d'arrêt
        supervisor.request_stop()
        await run_task
        assert supervisor.state == State.STOPPED


@pytest.mark.asyncio
async def test_supervisor_reconnection_on_connection_failure() -> None:
    """Vérifie que le superviseur passe par RECONNECTING avec backoff lors d'un échec."""
    states_visited: list[State] = []
    # Échoue 1 fois sur connect, puis réussit
    fake_streamer = FakeStreamer(fail_connect_count=1)
    fake_device = FakeDeviceConfig(name="Devialet Phantom")

    backoff_delays: list[float] = []

    async def fake_sleep(duration: float) -> None:
        backoff_delays.append(duration)

    supervisor = Supervisor(
        target_name="Devialet Phantom",
        on_state_change=states_visited.append,
        sleep_func=fake_sleep,
    )
    supervisor._streamer = fake_streamer  # type: ignore[assignment]

    with (
        patch("airplay_sender.supervisor.pyatv.scan", new=AsyncMock(return_value=[fake_device])),
        patch("airplay_sender.supervisor.LoopbackCapture", return_value=_create_mock_capture()),
        patch("airplay_sender.supervisor.AudioDeviceWatcher"),
    ):
        run_task = asyncio.create_task(supervisor.run())
        await asyncio.sleep(0.05)

        assert State.RECONNECTING in states_visited
        assert len(backoff_delays) >= 1
        assert supervisor.metrics.reconnect_count >= 1

        supervisor.request_stop()
        await run_task
        assert supervisor.state == State.STOPPED


@pytest.mark.asyncio
async def test_supervisor_switch_target_device() -> None:
    """Vérifie le changement dynamique de cible de diffusion."""
    states_visited: list[State] = []
    fake_streamer = FakeStreamer()
    dev1 = FakeDeviceConfig(name="Devialet Salon")
    dev2 = FakeDeviceConfig(name="Devialet Chambre")

    supervisor = Supervisor(
        target_name="Devialet Salon",
        on_state_change=states_visited.append,
        sleep_func=AsyncMock(),
    )
    supervisor._streamer = fake_streamer  # type: ignore[assignment]

    with (
        patch("airplay_sender.supervisor.pyatv.scan", new=AsyncMock(return_value=[dev1, dev2])),
        patch("airplay_sender.supervisor.LoopbackCapture", return_value=_create_mock_capture()),
        patch("airplay_sender.supervisor.AudioDeviceWatcher"),
    ):
        run_task = asyncio.create_task(supervisor.run())
        await asyncio.sleep(0.05)
        assert supervisor.state == State.STREAMING

        # Basculer vers Chambre
        supervisor.request_switch_device("Devialet Chambre")
        await asyncio.sleep(0.05)
        assert supervisor.target_name == "Devialet Chambre"
        assert supervisor.state == State.STREAMING

        supervisor.request_stop()
        await run_task
        assert supervisor.state == State.STOPPED
