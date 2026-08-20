from __future__ import annotations

import json
from pathlib import Path

import desktop.startup as startup


def test_imports_dpapi_protected_credentials(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "portable"
    source = tmp_path / "legacy" / "credentials.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps({"agent_id": "a1", "agent_token_protected": "encrypted"}),
        encoding="utf-8",
    )
    protected: list[Path] = []
    monkeypatch.setattr(startup, "PROJECT_ROOT", project)
    monkeypatch.setattr(startup, "protect_agent_directory", lambda path: (path.mkdir(parents=True), protected.append(path)))

    result = startup.import_existing_credentials([source])

    assert result.imported is True
    assert result.error == ""
    assert protected == [project / "agent_data"]
    assert json.loads((project / "agent_data" / "credentials.json").read_text(encoding="utf-8")) == {
        "agent_id": "a1",
        "agent_token_protected": "encrypted",
    }


def test_rejects_plaintext_agent_token(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "portable"
    source = tmp_path / "legacy" / "credentials.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps({"agent_id": "a1", "agent_token": "plaintext"}), encoding="utf-8"
    )
    monkeypatch.setattr(startup, "PROJECT_ROOT", project)

    result = startup.import_existing_credentials([source])

    assert result.imported is False
    assert "明文" in result.error
    assert not (project / "agent_data" / "credentials.json").exists()


def test_existing_target_is_not_replaced(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "portable"
    target = project / "agent_data" / "credentials.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"existing": true}', encoding="utf-8")
    source = tmp_path / "legacy.json"
    source.write_text('{"agent_token_protected": "encrypted"}', encoding="utf-8")
    monkeypatch.setattr(startup, "PROJECT_ROOT", project)

    result = startup.import_existing_credentials([source])

    assert result.imported is False
    assert target.read_text(encoding="utf-8") == '{"existing": true}'
