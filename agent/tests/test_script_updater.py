import hashlib
import importlib
import json
from pathlib import Path

import pytest

from agent import script_updater


class _Response:
    status = 200

    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.payload if limit < 0 else self.payload[:limit]


def test_check_and_update_engine_pinned_download(monkeypatch, tmp_path: Path):
    code = b"# safe test module\nVALUE = 1\n"
    target = tmp_path / "x_automation_engine.py"
    target.write_bytes(b"VALUE = 0\n")
    monkeypatch.setenv("LAOGU_ENGINE_UPDATE_SHA256", hashlib.sha256(code).hexdigest())
    monkeypatch.setattr(script_updater.urllib.request, "urlopen", lambda request, timeout: _Response(code))

    assert script_updater.check_and_update_engine("https://api.jaycwl.org/engine.py", str(target)) is True
    assert target.read_bytes() == code
    assert script_updater.check_and_update_engine("https://api.jaycwl.org/engine.py", str(target)) is False


def test_check_and_update_engine_rejects_unpinned_or_insecure(monkeypatch, tmp_path: Path):
    target = tmp_path / "engine.py"
    target.write_bytes(b"VALUE = 0\n")
    monkeypatch.delenv("LAOGU_ENGINE_UPDATE_SHA256", raising=False)
    assert script_updater.check_and_update_engine("http://api.jaycwl.org/engine.py", str(target)) is False
    assert target.read_bytes() == b"VALUE = 0\n"


def test_get_automation_engine_class_reloads_module():
    module = importlib.import_module("agent.x_automation_engine")
    original = script_updater.get_automation_engine_class()
    assert original is module.XAutomationEngine


def test_install_and_load_versioned_engine_cache(tmp_path: Path):
    first = b"class XAutomationEngine:\n    async def run(self, custom_config=None):\n        return {'version': 1}\n"
    second = b"class XAutomationEngine:\n    async def run(self, custom_config=None):\n        return {'version': 2}\n"
    manifest = {
        "version": "0.21.7",
        "sha256": hashlib.sha256(first).hexdigest(),
        "size": len(first),
        "read_only": True,
    }
    assert script_updater.install_engine_update(manifest, first, tmp_path) is True
    state = json.loads((tmp_path / "active.json").read_text(encoding="utf-8"))
    assert state["active_sha256"] == manifest["sha256"]
    assert script_updater.get_cached_automation_engine_class(tmp_path) is not None

    manifest["version"] = "0.21.8"
    manifest["sha256"] = hashlib.sha256(second).hexdigest()
    manifest["size"] = len(second)
    assert script_updater.install_engine_update(manifest, second, tmp_path) is True
    active = script_updater.get_cached_automation_engine_class(tmp_path)
    assert active is not None

    # A damaged active file automatically falls back to the previous known-good
    # version and rewrites active.json to make the rollback persistent.
    current = json.loads((tmp_path / "active.json").read_text(encoding="utf-8"))
    (tmp_path / current["active_path"]).write_bytes(b"broken")
    rolled_back = script_updater.get_cached_automation_engine_class(tmp_path)
    assert rolled_back is not None
    recovered = json.loads((tmp_path / "active.json").read_text(encoding="utf-8"))
    assert recovered["active_sha256"] == hashlib.sha256(first).hexdigest()


def test_sync_redownloads_a_damaged_only_cached_version(tmp_path: Path):
    source = b"class XAutomationEngine:\n    async def run(self, custom_config=None):\n        return {'ok': True}\n"
    digest = hashlib.sha256(source).hexdigest()
    manifest = {
        "version": "0.21.7",
        "sha256": digest,
        "size": len(source),
        "read_only": True,
        "source_url": "/api/agent/engine/source",
    }

    class Client:
        downloads = 0

        def fetch_engine_manifest(self):
            return manifest

        def fetch_engine_source(self, source_url):
            assert source_url == "/api/agent/engine/source"
            self.downloads += 1
            return source

    client = Client()
    assert script_updater.sync_engine_from_server(client, tmp_path) is True
    state = script_updater.read_engine_state(tmp_path)
    active = tmp_path / state["active_path"]
    active.write_bytes(b"damaged")

    assert script_updater.sync_engine_from_server(client, tmp_path) is True
    assert active.read_bytes() == source
    assert client.downloads == 2


def test_install_rejects_invalid_manifest_size(tmp_path: Path):
    source = b"class XAutomationEngine:\n    async def run(self):\n        return {}\n"
    manifest = {
        "version": "0.21.7",
        "sha256": hashlib.sha256(source).hexdigest(),
        "size": "not-a-number",
        "read_only": True,
    }
    with pytest.raises(script_updater.EngineUpdateError, match="manifest size"):
        script_updater.install_engine_update(manifest, source, tmp_path)


def test_admin_trusted_engine_can_use_system_and_network_modules(tmp_path: Path):
    source = (
        b"import os\n"
        b"import urllib.request\n\n"
        b"class XAutomationEngine:\n"
        b"    async def run(self, custom_config=None):\n"
        b"        return os.environ.get('LAOGU_TEST_VALUE', '')\n"
    )
    manifest = {
        "engine_id": "trusted-test",
        "version": "1.0.0",
        "sha256": hashlib.sha256(source).hexdigest(),
        "size": len(source),
        "read_only": True,
        "trusted_by_admin": True,
        "security_warnings": ["os", "urllib"],
    }

    assert script_updater.install_engine_update(manifest, source, tmp_path) is True
    state = script_updater.read_engine_state(tmp_path)
    assert state["trusted_by_admin"] is True
    assert state["security_warnings"] == ["os", "urllib"]
    assert script_updater.get_cached_automation_engine_class(tmp_path) is not None

    safe_source = b"class XAutomationEngine:\n    async def run(self):\n        return 'safe'\n"
    safe_manifest = {
        "version": "1.0.1",
        "sha256": hashlib.sha256(safe_source).hexdigest(),
        "size": len(safe_source),
        "read_only": True,
    }
    assert script_updater.install_engine_update(safe_manifest, safe_source, tmp_path) is True
    active_state = script_updater.read_engine_state(tmp_path)
    (tmp_path / active_state["active_path"]).write_bytes(b"damaged")
    assert script_updater.get_cached_automation_engine_class(tmp_path) is not None
    rolled_back = script_updater.read_engine_state(tmp_path)
    assert rolled_back["trusted_by_admin"] is True
    assert rolled_back["security_warnings"] == ["os", "urllib"]


def test_untrusted_engine_still_rejects_system_modules(tmp_path: Path):
    source = b"import os\nclass XAutomationEngine:\n    async def run(self):\n        return {}\n"
    manifest = {
        "version": "1.0.0",
        "sha256": hashlib.sha256(source).hexdigest(),
        "size": len(source),
        "read_only": True,
    }

    with pytest.raises(script_updater.EngineUpdateError, match="blocked import"):
        script_updater.install_engine_update(manifest, source, tmp_path)
