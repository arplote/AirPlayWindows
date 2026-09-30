"""Tests unitaires pour l'interface en ligne de commande (cli.py et __main__.py)."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pyatv.const import PairingRequirement, Protocol

from airplay_sender.__main__ import build_parser
from airplay_sender.cli import cmd_diag, cmd_pair, cmd_scan
from airplay_sender.config import AppConfig


class FakeService:
    def __init__(self, protocol: Protocol, port: int, pairing: PairingRequirement) -> None:
        self.protocol = protocol
        self.port = port
        self.pairing = pairing
        self.credentials = None


class FakeDeviceInfo:
    def __init__(self, model: str = "Devialet Phantom II") -> None:
        self.model = model
        self.model_str = model


class MockConfigWithServices:
    def __init__(self, name: str = "Devialet Phantom II", ip: str = "192.168.1.42") -> None:
        self.name = name
        self.address = ip
        self.identifier = "AA:BB:CC:DD:EE:FF"
        self.services = [
            FakeService(Protocol.RAOP, 7000, PairingRequirement.NotNeeded),
            FakeService(Protocol.AirPlay, 7000, PairingRequirement.NotNeeded),
        ]
        self.device_info = FakeDeviceInfo()

    def get_service(self, protocol: Protocol) -> FakeService | None:
        for s in self.services:
            if s.protocol == protocol:
                return s
        return None


def test_cli_parser_scan() -> None:
    parser = build_parser()
    args = parser.parse_args(["scan", "--timeout", "10", "--json"])
    assert args.subcommand == "scan"
    assert args.timeout == 10
    assert args.json is True


def test_cli_parser_run() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["run", "--device", "Living Room", "--volume", "0.5", "--buffer-ms", "1200"]
    )
    assert args.subcommand == "run"
    assert args.device == "Living Room"
    assert args.volume == 0.5
    assert args.buffer_ms == 1200


def test_cli_parser_diag() -> None:
    parser = build_parser()
    args = parser.parse_args(["diag", "-o", "report.txt"])
    assert args.subcommand == "diag"
    assert args.output == Path("report.txt")


@pytest.mark.asyncio
async def test_cmd_scan_with_save(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config = AppConfig()

    fake_device = MockConfigWithServices(name="Devialet Test", ip="192.168.1.42")

    args = argparse.Namespace(
        timeout=2,
        json=False,
        save="Devialet Test",
        config=config_file,
    )

    with patch("airplay_sender.cli.pyatv.scan", new=AsyncMock(return_value=[fake_device])):
        ret = await cmd_scan(args, config)
        assert ret == 0
        assert config.device.name == "Devialet Test"
        assert config.device.last_known_ip == "192.168.1.42"
        assert config_file.exists()


@pytest.mark.asyncio
async def test_cmd_pair_not_needed(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config = AppConfig()

    fake_device = MockConfigWithServices(name="Devialet Phantom II", ip="192.168.1.42")

    args = argparse.Namespace(
        device="Devialet Phantom II",
        config=config_file,
    )

    with patch("airplay_sender.cli.pyatv.scan", new=AsyncMock(return_value=[fake_device])):
        ret = await cmd_pair(args, config)
        assert ret == 0
        assert config.device.name == "Devialet Phantom II"


@pytest.mark.asyncio
async def test_cmd_diag_writes_file(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    out_file = tmp_path / "diag_out.txt"
    config = AppConfig()

    args = argparse.Namespace(
        config=config_file,
        output=out_file,
    )

    with (
        patch("airplay_sender.diagnostics.pyatv.scan", new=AsyncMock(return_value=[])),
        patch(
            "airplay_sender.diagnostics.gather_audio_devices", return_value={"loopback_devices": []}
        ),
    ):
        ret = await cmd_diag(args, config)
        assert ret == 0
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "RAPPORT DE DIAGNOSTIC" in content
