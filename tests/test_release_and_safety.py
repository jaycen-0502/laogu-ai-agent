from common.release import DEFAULT_VERSION, release_info
from common.safety_policy import evaluate_task


def test_release_metadata_is_non_sensitive_and_consistent():
    info = release_info(component="test")
    assert info["version"] == DEFAULT_VERSION
    assert info["component"] == "test"
    assert "token" not in str(info).lower()


def test_safety_policy_blocks_high_risk_automation_without_logging_input():
    assert evaluate_task("x.search", {"query": "public documentation"}).allowed
    decision = evaluate_task("script.execute", {"description": "bulk DM followers"})
    assert not decision.allowed
    assert decision.code == "HIGH_RISK_AUTOMATION"

