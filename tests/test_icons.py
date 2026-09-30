"""Tests unitaires pour la génération dynamique des icônes (icons.py)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from airplay_sender.icons import create_tray_icon_image, get_icon, save_ico_file


def test_create_tray_icon_image() -> None:
    img = create_tray_icon_image("#2ECC71", size=64)
    assert isinstance(img, Image.Image)
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_get_icon_caching() -> None:
    img1 = get_icon("STREAMING")
    img2 = get_icon("streaming")
    assert img1 is img2
    assert img1.size == (64, 64)


def test_save_ico_file(tmp_path: Path) -> None:
    ico_path = tmp_path / "test_icon.ico"
    res = save_ico_file(ico_path, color_hex="#2ECC71")
    assert res == ico_path
    assert ico_path.exists()
    assert ico_path.stat().st_size > 0
