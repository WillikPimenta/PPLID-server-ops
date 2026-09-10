"""Testes do módulo tray_install_icons (templates AppData)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.services import tray_install_icons as icons


def _write_rgba(path: Path, size: tuple[int, int], color: tuple[int, int, int, int]) -> None:
    img = Image.new("RGBA", size, color)
    img.save(path, format="PNG")


def test_resolve_onedrive_dir_picks_latest_version(tmp_path: Path):
    base = tmp_path / "OneDrive"
    (base / "24.001.0001.0001").mkdir(parents=True)
    (base / "26.106.0603.0003").mkdir(parents=True)
    assert icons.resolve_onedrive_dir(base).name == "26.106.0603.0003"


def test_list_state_icon_paths_onedrive_only_appdata_pngs(tmp_path: Path):
    local = tmp_path / "imagens" / "onedrive"
    local.mkdir(parents=True)
    _write_rgba(local / "disconnected.png", (46, 46), (0, 0, 255, 255))
    _write_rgba(local / "AppErrorBlue.png", (32, 32), (255, 0, 0, 255))

    paths = icons.list_state_icon_paths("onedrive", "disconnected", repo_templates_dir=local)
    names = [p.name for p in paths]
    assert names == ["disconnected.png"]
    assert "AppErrorBlue.png" not in names


def test_list_state_icon_paths_ignores_fallback_templates_dir(tmp_path: Path):
    local = tmp_path / "local"
    fallback = tmp_path / "fallback"
    local.mkdir()
    fallback.mkdir()
    _write_rgba(fallback / "disconnected.png", (46, 46), (0, 0, 255, 255))

    paths = icons.list_state_icon_paths(
        "onedrive",
        "disconnected",
        repo_templates_dir=local,
        fallback_templates_dir=fallback,
    )
    assert paths == []


def test_list_state_icon_paths_cisco_appdata_only(tmp_path: Path):
    local = tmp_path / "imagens" / "cisco"
    local.mkdir(parents=True)
    _write_rgba(local / "connected.png", (46, 46), (0, 120, 255, 255))
    _write_rgba(local / "vpn_connected.ico", (32, 32), (0, 200, 0, 255))

    paths = icons.list_state_icon_paths("cisco", "connected", repo_templates_dir=local)
    assert [p.name for p in paths] == ["connected.png"]


def test_load_tray_icon_produces_mask(tmp_path: Path):
    path = tmp_path / "icon.png"
    _write_rgba(path, (20, 20), (255, 0, 0, 255))
    icons.clear_cache()
    icon = icons.load_tray_icon(path, state="connected")
    assert icon is not None
    assert icon.bgr.shape[:2] == icon.mask.shape[:2]
    assert int(icon.mask.sum()) > 0
    assert icon.kind == "conn"


def test_load_tray_icon_uses_cache(tmp_path: Path):
    path = tmp_path / "icon.png"
    _write_rgba(path, (16, 16), (0, 255, 0, 255))
    icons.clear_cache()
    first = icons.load_tray_icon(path, state="connected")
    second = icons.load_tray_icon(path, state="connected")
    assert first is second


def test_has_minimum_icons_false_without_connected(tmp_path: Path):
    assert icons.has_minimum_icons("onedrive", repo_templates_dir=tmp_path) is False


def test_has_minimum_icons_true_with_connected_png(tmp_path: Path):
    _write_rgba(tmp_path / "connected.png", (24, 24), (0, 0, 255, 255))
    assert icons.has_minimum_icons("onedrive", repo_templates_dir=tmp_path) is True


def test_effective_target_height_keeps_tray_icons_native():
    assert icons._effective_target_height(32, 48) == 32
    assert icons._effective_target_height(256, 48) == 48


def test_install_status_reports_appdata_paths(tmp_path: Path):
    _write_rgba(tmp_path / "connected.png", (24, 24), (0, 0, 255, 255))
    status = icons.install_status("onedrive", repo_templates_dir=tmp_path)
    assert status["ready"] is True
    assert status["install_base"] is None
    assert status["templates_dir"] == str(tmp_path)
    assert "connected.png" in status["states"]["connected"][0]
