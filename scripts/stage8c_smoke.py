"""Run the harmless Stage 8C managed script against real Laogu Profiles.

This utility talks only to the local Laogu API and opens Example Domain.  It
does not read or modify X account data.
"""

from __future__ import annotations

import argparse
import json
from uuid import uuid4

from common.script_validation import source_sha256
from agent.config import load_settings
from agent.laogu_api import LaoguApi
from agent.task_service import TaskService


SOURCE = """module.exports.run = async ({ useBrowser, log, params }) => {
  log("script started");
  const url = "https://example.com";
  const timeoutMs = Number(params.timeoutMs) || 30000;
  const runtime = await useBrowser();
  const page = runtime.page;
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: timeoutMs });
  const title = await page.title();
  const finalUrl = page.url();
  log("script finished");
  return { success: true, title, url: finalUrl, params };
};
"""

SCHEMA = {
    "type": "object",
    "properties": {"smoke": {"type": "string", "maxLength": 30}},
    "additionalProperties": False,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, choices=(1, 2), default=2)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--profile-name", action="append", default=[])
    parser.add_argument("--profile-id", action="append", default=[])
    args = parser.parse_args()
    settings = load_settings()
    profiles = LaoguApi(settings).list_profiles()
    candidates = [item for item in profiles if item.get("profileId")]
    if not candidates:
        print(json.dumps({"ok": False, "reason": "No real Laogu Profile found"}, ensure_ascii=False))
        return 2

    # Prefer the user's named test Profiles but never assume an ID exists.
    candidates.sort(key=lambda item: (str(item.get("profileName")) not in {"11", "22"}, str(item.get("profileName"))))
    if args.profile_name:
        requested = set(args.profile_name)
        candidates = [item for item in candidates if str(item.get("profileName")) in requested]
    if args.profile_id:
        requested_ids = set(args.profile_id)
        candidates = [item for item in candidates if str(item.get("profileId")) in requested_ids]
    selected = candidates[: args.limit]
    service = TaskService()
    tasks = []
    for profile in selected:
        task_id = f"stage8c-{uuid4().hex[:10]}"
        task = service.create_task(
            str(profile["profileId"]),
            "script.execute",
            {
                "script_id": "stage8c-example-domain",
                "script_version_id": "stage8c-example-domain-v1",
                "params": {"smoke": "stage8c"},
                "timeout": args.timeout,
            },
            task_id=task_id,
            timeout_seconds=args.timeout,
        )
        task.profile_name = str(profile.get("profileName") or profile["profileId"])
        task.metadata["script_bundle"] = {
            "script_id": "stage8c-example-domain",
            "script_version_id": "stage8c-example-domain-v1",
            "language": "javascript",
            "source": SOURCE,
            "params_schema": SCHEMA,
            "sha256": source_sha256(SOURCE),
        }
        tasks.append(task)

    completed = service.task_manager.run_concurrent(tasks)
    items = []
    for task in completed:
        managed = task.result or {}
        result = managed.get("result", {}) if isinstance(managed, dict) else {}
        items.append(
            {
                "task_id": task.task_id,
                "profile_id": task.profile_id,
                "profile_name": task.profile_name,
                "status": task.status.value,
                "duration": task.elapsed_time,
                "title": result.get("title") if isinstance(result, dict) else None,
                "url": result.get("url") if isinstance(result, dict) else None,
                "error": task.error,
            }
        )
    ok = bool(items) and all(item["status"] == "SUCCESS" and item["title"] == "Example Domain" for item in items)
    print(json.dumps({"ok": ok, "profile_count": len(items), "items": items}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
