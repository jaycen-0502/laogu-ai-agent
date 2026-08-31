from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from desktop.controller import DesktopController


class FakeApi:
    pass


class FakeDiscovery:
    def scan(self, profile_ids=None):
        return []


class FakeRegistry:
    def list(self):
        return []


class FakeAgentService:
    def start(self):
        pass

    def status(self):
        return {"server": "ONLINE", "agent": "ONLINE"}

    def has_capability(self, capability):
        return capability in {"local.browser.control", "automation.run"}


class SlowReadyBrowser:
    def __init__(self):
        self.active = 0
        self.maximum = 0
        self.order: list[str] = []
        self.guard = threading.Lock()

    def start_profile_ready(self, profile_id, timeout_seconds, *, retries, progress):
        with self.guard:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.order.append(str(profile_id))
        progress("PROFILE_STARTING")
        time.sleep(0.02)
        with self.guard:
            self.active -= 1
        progress("PROFILE_READY source=start_response")
        return {"cdpUrl": f"http://127.0.0.1:{9200 + len(self.order)}"}


def test_controller_serializes_browser_creation_and_releases_slot():
    browser = SlowReadyBrowser()
    controller = DesktopController(
        api=FakeApi(),
        browser_manager=browser,
        discovery=FakeDiscovery(),
        registry=FakeRegistry(),
        agent_service=FakeAgentService(),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(controller.start_profile, ["profile-a", "profile-b"]))
    assert [item["cdpUrl"] for item in results] == ["http://127.0.0.1:9201", "http://127.0.0.1:9202"]
    assert browser.maximum == 1


class FailingThenReadyBrowser:
    def __init__(self):
        self.calls: list[str] = []

    def start_profile_ready(self, profile_id, timeout_seconds, *, retries, progress):
        self.calls.append(str(profile_id))
        progress("PROFILE_STARTING")
        if profile_id == "profile-a":
            progress("PROFILE_START_FAILED attempt=3 error=unavailable")
            raise RuntimeError("unavailable")
        progress("PROFILE_READY source=start_response")
        return {"cdpUrl": "http://127.0.0.1:9222"}


def test_failed_profile_releases_start_slot_for_next_profile():
    browser = FailingThenReadyBrowser()
    controller = DesktopController(
        api=FakeApi(),
        browser_manager=browser,
        discovery=FakeDiscovery(),
        registry=FakeRegistry(),
        agent_service=FakeAgentService(),
    )
    with __import__("pytest").raises(RuntimeError):
        controller.start_profile("profile-a")
    assert controller.start_profile("profile-b")["cdpUrl"].endswith(":9222")
    assert browser.calls == ["profile-a", "profile-b"]


class SerializedBrowser:
    def __init__(self):
        self.active = 0
        self.maximum = 0
        self.guard = threading.Lock()

    def run_automation(self, *, profile_id, url, timeout_seconds):
        with self.guard:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        time.sleep(0.01)
        with self.guard:
            self.active -= 1
        return {"ok": True, "result": {"url": url}}


def test_task_manager_serializes_same_profile_but_allows_other_profiles():
    from agent.task_manager import TaskManager

    manager = TaskManager(SerializedBrowser(), logging.getLogger("startup-scheduling"), max_workers=3)
    first = manager.create_task(profile_id="same", profile_name="A", url="https://a", timeout_seconds=1)
    second = manager.create_task(profile_id="same", profile_name="A", url="https://b", timeout_seconds=1)
    other = manager.create_task(profile_id="other", profile_name="B", url="https://c", timeout_seconds=1)
    results = manager.run_concurrent([first, second, other])
    assert {item.status.value for item in results} == {"SUCCESS"}
