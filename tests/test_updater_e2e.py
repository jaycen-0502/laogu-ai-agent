from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import zipfile

import pytest

import updater
from desktop.updater import ReleaseInfo, _display_size, DownloadWorker
from server.app_update_api import _version_key, _is_newer, _validate_version, _validate_channel


def test_version_comparison():
    assert _version_key("0.21.92") > _version_key("0.21.91")
    assert _version_key("0.21.100") > _version_key("0.21.92")
    assert _version_key("v0.21.92") == _version_key("0.21.92")
    assert _is_newer("0.21.92", "0.21.91") is True
    assert _is_newer("0.21.91", "0.21.92") is False
    assert _is_newer("0.21.92", "0.21.92") is False


def test_version_validation():
    assert _validate_version("0.21.92") == "0.21.92"
    assert _validate_version("v1.0.0-beta.1") == "v1.0.0-beta.1"
    with pytest.raises(Exception):
        _validate_version("")
    with pytest.raises(Exception):
        _validate_version("invalid version with spaces")


def test_channel_validation():
    assert _validate_channel("stable") == "stable"
    assert _validate_channel("beta") == "beta"
    with pytest.raises(Exception):
        _validate_channel("bad channel!!!")


def test_release_info_parsing():
    payload = {
        "has_update": True,
        "latest_version": "0.21.92",
        "is_mandatory": False,
        "release_notes": "Bug fixes",
        "download_url": "/api/v1/app/releases/0.21.92/package",
        "sha256": "51D96DCC",
        "file_size": 1024000,
    }
    info = ReleaseInfo.from_mapping(payload)
    assert info.has_update is True
    assert info.latest_version == "0.21.92"
    assert info.sha256 == "51d96dcc"
    assert info.file_size == 1024000

    # Malformed payload should not crash
    empty_info = ReleaseInfo.from_mapping(None)
    assert empty_info.has_update is False
    assert empty_info.latest_version == ""
    assert empty_info.file_size == 0


def test_display_size():
    assert _display_size(500) == "500 B"
    assert "KB" in _display_size(2048)
    assert "MB" in _display_size(10 * 1024 * 1024)
    assert "GB" in _display_size(2 * 1024 * 1024 * 1024)


def test_updater_whitelist_protection():
    assert updater.is_protected_member("config/laogu.env") is True
    assert updater.is_protected_member("agent_data/agent_state.db") is True
    assert updater.is_protected_member("credentials.json") is True
    assert updater.is_protected_member("runtime_config.json") is True
    assert updater.is_protected_member("telegram_config.json") is True
    assert updater.is_protected_member("logs/app.log") is True
    assert updater.is_protected_member("Laogu-Desktop.exe") is False
    assert updater.is_protected_member("_internal/python313.dll") is False


def test_updater_apply_update_and_safe_destination(tmp_path: Path):
    target = tmp_path / "app"
    target.mkdir()

    # Pre-existing user files that MUST NOT be touched
    config_dir = target / "config"
    config_dir.mkdir()
    user_env = config_dir / "laogu.env"
    user_env.write_text("MY_SECRET=123", encoding="utf-8")

    # Create dummy update zip
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as z:
        z.writestr("Laogu-Desktop.exe", "new-binary")
        z.writestr("config/laogu.env", "OVERWRITTEN_ENV")
        z.writestr("new_module.dll", "module-content")

    zip_file = tmp_path / "update.zip"
    zip_file.write_bytes(zip_buf.getvalue())

    # Apply update
    updater.apply_update(zip_file, target)

    # Verify new binary was written
    assert (target / "Laogu-Desktop.exe").read_text(encoding="utf-8") == "new-binary"
    assert (target / "new_module.dll").read_text(encoding="utf-8") == "module-content"
    # Verify protected config was NOT overwritten
    assert user_env.read_text(encoding="utf-8") == "MY_SECRET=123"


def test_updater_path_traversal_blocked(tmp_path: Path):
    target = tmp_path / "app"
    target.mkdir()

    with pytest.raises(ValueError):
        updater._safe_destination(target, "../../../evil.txt")
