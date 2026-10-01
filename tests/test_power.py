"""Tests de la gestion de la veille Windows (power.py et supervisor.py)."""

from __future__ import annotations

import asyncio

import pytest

from airplay_sender.power import (
    PBT_APMRESUMEAUTOMATIC,
    PBT_APMRESUMESUSPEND,
    PBT_APMSUSPEND,
    PowerNotifier,
)
from airplay_sender.supervisor import Supervisor


def test_power_notifier_routes_events() -> None:
    calls: list[str] = []
    notifier = PowerNotifier(lambda: calls.append("suspend"), lambda: calls.append("resume"))

    notifier.handle_event(PBT_APMSUSPEND)
    notifier.handle_event(PBT_APMRESUMEAUTOMATIC)
    notifier.handle_event(PBT_APMRESUMESUSPEND)
    notifier.handle_event(0x99)  # évènement ignoré

    assert calls == ["suspend", "resume", "resume"]


def test_power_notifier_swallows_callback_errors() -> None:
    def boom() -> None:
        raise RuntimeError("boom")

    PowerNotifier(boom, boom).handle_event(PBT_APMSUSPEND)  # ne doit pas lever


@pytest.mark.asyncio
async def test_supervisor_suspend_cancels_stream_and_resume_clears_flag() -> None:
    supervisor = Supervisor(target_name="Test")
    supervisor._stream_task = asyncio.create_task(asyncio.sleep(60))

    supervisor._enter_suspend()
    assert supervisor._suspended
    with pytest.raises(asyncio.CancelledError):
        await supervisor._stream_task

    supervisor._exit_suspend()
    assert not supervisor._suspended


@pytest.mark.asyncio
async def test_supervisor_pause_waits_for_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor = Supervisor(target_name="Test")
    monkeypatch.setattr(Supervisor, "RESUME_SETTLE_SEC", 0.0)
    supervisor._suspended = True

    task = asyncio.create_task(supervisor._pause_for_suspend())
    await asyncio.sleep(0.1)
    assert not task.done()  # tant que Windows dort, on reste en pause

    supervisor._exit_suspend()
    await asyncio.wait_for(task, timeout=2.0)
