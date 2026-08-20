from pathlib import Path

from agent.runtime_config import RuntimeConfig


def test_runtime_config_versions_and_modes(tmp_path: Path):
    config = RuntimeConfig(tmp_path / "runtime.json")
    first = config.update("profile-1", {"query": "a"}, mode="NEXT_RUN")
    assert first["version"] == 1
    assert first["next_run"]["query"] == "a"
    second = config.update("profile-1", {"query": "b"}, mode="HOT_UPDATE")
    assert second["version"] == 2
    assert second["active"]["query"] == "b"
    assert config.snapshot("profile-1")["next_run"]["query"] == "a"
