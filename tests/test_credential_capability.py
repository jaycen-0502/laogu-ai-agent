from agent.laogu_api import LaoguApi


def test_probe_uses_advertised_metadata_without_returning_values():
    api = object.__new__(LaoguApi)
    api.health = lambda: {"ok": True, "capabilities": {"cookieReadSupported": True, "cookieWriteSupported": False, "credentialSnapshotSupported": True, "cookieValue": "secret"}}
    api.profile_status = lambda profile_id: {"status": "RUNNING", "cookies": [{"value": "secret"}]}
    result = api.probe_credential_capability("p1")
    assert result == {
        "probe_version": "1",
        "browser_reachable": True,
        "cookie_read_supported": True,
        "cookie_write_supported": False,
        "credential_snapshot_allowed": True,
        "evidence": "ADVERTISED_CAPABILITY_METADATA",
    }
    assert "secret" not in str(result)
