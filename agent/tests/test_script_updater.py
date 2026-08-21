import hashlib
import importlib
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
