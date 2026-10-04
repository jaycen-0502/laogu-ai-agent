from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from agent.account_registry import AccountRegistry
from agent.models import (
    AccountStatus,
    BrowserStatus,
    DiscoveredAccount,
    LoginStatus,
)


def discovered(
    profile_id: str,
    *,
    username: str = "",
    account_id: str = "",
    login: LoginStatus = LoginStatus.UNKNOWN,
    browser: BrowserStatus = BrowserStatus.RUNNING,
    checked: datetime | None = None,
) -> DiscoveredAccount:
    return DiscoveredAccount(
        profile_id=profile_id,
        instance_id=profile_id,
        profile_name=profile_id,
        browser_status=browser,
        login_status=login,
        x_username=username,
        x_account_id=account_id,
        last_checked=checked or datetime.now().astimezone(),
    )


class AccountRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.registry_path = root / "registry.json"
        self.history_path = root / "history.jsonl"
        self.registry = AccountRegistry(self.registry_path, self.history_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_profile_switch_updates_mapping_and_timestamp(self):
        first_checked = datetime.now().astimezone() - timedelta(minutes=2)
        first = self.registry.update(
            discovered(
                "p1",
                username="@first",
                account_id="1001",
                login=LoginStatus.LOGGED_IN,
                checked=first_checked,
            )
        )
        first_mapping_time = first.mapping_updated_at
        second = self.registry.update(
            discovered(
                "p1",
                username="@second",
                account_id="2002",
                login=LoginStatus.LOGGED_IN,
            )
        )
        self.assertEqual(second.x_username, "@second")
        self.assertEqual(second.x_account_id, "2002")
        self.assertGreaterEqual(second.mapping_updated_at, first_mapping_time)
        history = self.history_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(history), 2)

    def test_unknown_and_logged_out_preserve_historical_mapping(self):
        self.registry.update(
            discovered(
                "p1",
                username="@known",
                account_id="1001",
                login=LoginStatus.LOGGED_IN,
            )
        )
        unknown = self.registry.update(discovered("p1", login=LoginStatus.UNKNOWN))
        self.assertEqual(unknown.x_username, "@known")
        self.assertEqual(unknown.x_account_id, "1001")
        self.assertEqual(unknown.login_status, LoginStatus.LOGGED_IN)
        logged_out = self.registry.update(
            discovered("p1", login=LoginStatus.NOT_LOGGED_IN)
        )
        self.assertEqual(logged_out.x_username, "@known")
        self.assertEqual(logged_out.x_account_id, "1001")
        self.assertEqual(logged_out.login_status, LoginStatus.NOT_LOGGED_IN)
        self.assertEqual(logged_out.account_status, AccountStatus.UNKNOWN)

    def test_duplicate_account_id_marks_both_profiles(self):
        records = self.registry.update_many(
            [
                discovered(
                    "p1",
                    username="@one",
                    account_id="1001",
                    login=LoginStatus.LOGGED_IN,
                ),
                discovered(
                    "p2",
                    username="@two",
                    account_id="1001",
                    login=LoginStatus.LOGGED_IN,
                ),
            ]
        )
        self.assertTrue(
            all(item.account_status is AccountStatus.DUPLICATE_ACCOUNT for item in records)
        )
        self.assertEqual(len(self.registry.find_by_x_account("1001")), 2)
        self.assertEqual(self.registry.find_by_x_account(""), [])

    def test_stopped_profile_is_preserved(self):
        record = self.registry.update(
            discovered(
                "p1",
                username="@known",
                account_id="1001",
                login=LoginStatus.LOGGED_IN,
                browser=BrowserStatus.STOPPED,
            )
        )
        self.assertEqual(record.browser_status, BrowserStatus.STOPPED)
        self.assertEqual(self.registry.find_by_profile("p1").profile_id, "p1")

    def test_update_profile_metadata_auto_registers_new_profiles(self):
        # When registry starts empty (fresh machine), update_profile_metadata should
        # automatically register newly seen profiles from the local browser.
        profiles = [
            {
                "profileId": "prof-new-1",
                "profileName": "11",
                "instanceId": "inst-1",
                "running": True,
                "proxyName": "代理万1",
                "proxyConfig": "http://127.0.0.1:7890",
            }
        ]
        changed = self.registry.update_profile_metadata(profiles)
        self.assertTrue(changed)
        records = self.registry.list()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.profile_id, "prof-new-1")
        self.assertEqual(record.profile_name, "11")
        self.assertEqual(record.browser_status, BrowserStatus.RUNNING)
        self.assertEqual(record.login_status, LoginStatus.UNKNOWN)
        self.assertEqual(record.proxy_name, "代理万1")

        # Second call with running=False updates browser status
        profiles[0]["running"] = False
        changed2 = self.registry.update_profile_metadata(profiles)
        self.assertTrue(changed2)
        self.assertEqual(self.registry.get("prof-new-1").browser_status, BrowserStatus.STOPPED)

    def test_remove_persists_deleted_profiles_and_prevents_reimport(self):
        profiles = [
            {
                "profileId": "prof-11",
                "profileName": "11",
                "running": False,
            },
            {
                "profileId": "prof-22",
                "profileName": "22",
                "running": False,
            },
        ]
        # Auto register both
        self.registry.update_profile_metadata(profiles)
        self.assertEqual(len(self.registry.list()), 2)

        # Remove prof-11
        self.assertTrue(self.registry.remove("prof-11"))
        self.assertEqual(len(self.registry.list()), 1)
        self.assertIn("prof-11", self.registry._deleted_profile_ids)

        # Refreshing profiles should NOT resurrect prof-11!
        self.registry.update_profile_metadata(profiles)
        self.assertEqual(len(self.registry.list()), 1)
        self.assertIsNone(self.registry.get("prof-11"))

        # Re-instantiate from file to test persistence
        registry2 = AccountRegistry(self.registry_path, self.history_path)
        self.assertIn("prof-11", registry2._deleted_profile_ids)
        self.assertEqual(len(registry2.list()), 1)

        # Refreshing on re-instantiated registry still does not resurrect prof-11
        registry2.update_profile_metadata(profiles)
        self.assertEqual(len(registry2.list()), 1)
        self.assertIsNone(registry2.get("prof-11"))


if __name__ == "__main__":
    unittest.main()
