import logging
from pathlib import Path
import tempfile
import unittest

from agent.account_discovery import AccountDiscovery
from agent.models import BrowserStatus, LoginStatus


class FakeBrowserManager:
    def __init__(self):
        self.calls = []
        self.round = 0
        self.profiles = [
            {"profileId": "p1", "profileName": "11", "running": False},
            {"profileId": "p2", "profileName": "22", "running": True},
            {"profileId": "p3", "profileName": "33", "running": None},
            {"profileId": "off", "profileName": "disabled", "enabled": False},
        ]

    def get_profiles(self):
        return list(self.profiles)

    def run_account_discovery(self, **kwargs):
        self.calls.append(kwargs)
        profile_id = kwargs["profile_id"]
        if profile_id == "p1":
            username = "first_user" if self.round == 0 else "changed_user"
            return {
                "ok": True,
                "status": "success",
                "result": {
                    "loginStatus": "LOGGED_IN",
                    "xUsername": "@" + username,
                    "xAccountId": "1001",
                    "identityVerified": True,
                    "url": "https://x.com/home",
                },
            }
        if profile_id == "p2":
            return {
                "ok": True,
                "status": "success",
                "result": {
                    "loginStatus": "NOT_LOGGED_IN",
                    "identityVerified": True,
                },
            }
        return {
            "ok": True,
            "status": "success",
            "result": {"title": "Home / X", "url": "https://x.com/home"},
        }


class AccountDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("account-discovery-test")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())

    def test_dynamic_discovery_and_conservative_statuses(self):
        manager = FakeBrowserManager()
        with tempfile.TemporaryDirectory() as temp_dir:
            result_file = Path(temp_dir) / "accounts.json"
            discovery = AccountDiscovery(
                manager,
                self.logger,
                hook_path="/api/automation/hooks/read-only-x-account",
                result_file=result_file,
                max_workers=3,
            )
            records = {item.profile_id: item for item in discovery.scan()}

            self.assertEqual(set(records), {"p1", "p2", "p3"})
            self.assertEqual(records["p1"].login_status, LoginStatus.LOGGED_IN)
            self.assertEqual(records["p1"].x_username, "@first_user")
            self.assertEqual(records["p1"].browser_status, BrowserStatus.STOPPED)
            self.assertEqual(records["p2"].login_status, LoginStatus.NOT_LOGGED_IN)
            self.assertEqual(records["p3"].login_status, LoginStatus.UNKNOWN)
            self.assertTrue(result_file.exists())
            self.assertTrue(all(call["url"] == "https://x.com/home" for call in manager.calls))

            manager.round = 1
            changed = {item.profile_id: item for item in discovery.scan(["p1"])}
            self.assertEqual(changed["p1"].x_username, "@changed_user")

    def test_unverified_identity_is_unknown(self):
        status, username, account_id, reason = AccountDiscovery._parse_identity(
            {"result": {"username": "@valid_name", "accountId": 42}}
        )
        self.assertEqual(status, LoginStatus.UNKNOWN)
        self.assertEqual(username, "")
        self.assertEqual(account_id, "")
        self.assertTrue(reason)

    def test_home_page_alone_does_not_prove_login(self):
        status, username, account_id, reason = AccountDiscovery._parse_identity(
            {"result": {"title": "Home / X", "url": "https://x.com/home"}}
        )
        self.assertEqual(status, LoginStatus.UNKNOWN)
        self.assertEqual(username, "")
        self.assertEqual(account_id, "")
        self.assertTrue(reason)

    def test_hook_timeout_is_isolated_and_marks_browser_error(self):
        class TimeoutManager(FakeBrowserManager):
            def run_account_discovery(self, **kwargs):
                if kwargs["profile_id"] == "p1":
                    raise TimeoutError("deliberate timeout")
                return super().run_account_discovery(**kwargs)

        manager = TimeoutManager()
        discovery = AccountDiscovery(
            manager,
            self.logger,
            hook_path="/api/automation/hooks/read-only-x-account",
            max_workers=3,
        )
        records = {item.profile_id: item for item in discovery.scan()}
        self.assertEqual(records["p1"].login_status, LoginStatus.UNKNOWN)
        self.assertEqual(records["p1"].browser_status, BrowserStatus.ERROR)
        self.assertIn("timeout", records["p1"].error)
        self.assertEqual(records["p2"].login_status, LoginStatus.NOT_LOGGED_IN)


if __name__ == "__main__":
    unittest.main()
