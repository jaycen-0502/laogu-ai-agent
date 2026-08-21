import hashlib
import importlib
import json
from pathlib import Path

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
