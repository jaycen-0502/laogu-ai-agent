from __future__ import annotations

import os

from agent import config


def test_portable_config_reads_allowlisted_values_without_overriding_environment(tmp_path, monkeypatch):
    portable = tmp_path / "portable"
    config_dir = portable / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "laogu.env").write_text(
        "LAOGU_SERVER_URL=https://configured.example\n"
        "LAOGU_BASE_URL=http://127.0.0.1:19876\n"
        "NOT_LAOGU=ignored\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "PROJECT_ROOT", portable)
    monkeypatch.setenv("LAOGU_SERVER_URL", "https://environment.example")
    monkeypatch.delenv("LAOGU_BASE_URL", raising=False)
    monkeypatch.delenv("NOT_LAOGU", raising=False)
    config._load_local_environment()
    assert os.environ["LAOGU_SERVER_URL"] == "https://environment.example"
    assert os.environ["LAOGU_BASE_URL"] == "http://127.0.0.1:19876"
    assert "NOT_LAOGU" not in os.environ
