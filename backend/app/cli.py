from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence


DEFAULT_API = "http://127.0.0.1:8766"


class CliError(RuntimeError):
    pass


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("detail", "")
            except (ValueError, AttributeError):
                detail = ""
            raise CliError(detail or "Backend returned HTTP %s" % exc.code) from None
        except (urllib.error.URLError, TimeoutError):
            raise CliError(
                "Cannot reach %s. Start it with: make run" % self.base_url
            ) from None
        return json.loads(raw) if raw else None

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("POST", path, body)


def _cell(value: Any, width: int) -> str:
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "yes" if value else "no"
    else:
        text = str(value).replace("\n", " ").strip()
    return text if len(text) <= width else text[: max(0, width - 3)] + "..."


def print_table(rows: Iterable[Dict[str, Any]], columns: Sequence[tuple[str, str, int]]) -> None:
    values = list(rows)
    if not values:
        print("No results.")
        return
    header = "  ".join(_cell(title, width).ljust(width) for _, title, width in columns)
    print(header)
    print("  ".join("-" * width for _, _, width in columns))
    for row in values:
        print("  ".join(_cell(row.get(key), width).ljust(width) for key, _, width in columns))


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def cmd_onboard(client: ApiClient, args: argparse.Namespace) -> int:
    token = args.token or getpass.getpass("Quercus API token (input hidden): ").strip()
    if not token:
        raise CliError("A Quercus API token is required")
    timezone = args.timezone or input("Timezone [America/Toronto]: ").strip() or "America/Toronto"
    offsets_raw = args.reminders or input("Reminder days before due date [7,3,1]: ").strip() or "7,3,1"
    try:
        offsets = [int(value.strip()) for value in offsets_raw.split(",") if value.strip()]
    except ValueError:
        raise CliError("Reminder offsets must be comma-separated whole numbers") from None
    latest_before = (client.get("/api/sync/status").get("latest") or {}).get("id")
    result = client.post("/api/onboarding", {
        "quercus_api_token": token,
        "timezone": timezone,
        "reminder_offsets_days": offsets,
    })
    print("Onboarded %s. Initial sync queued." % result["user"].get("name", "Quercus user"))
    if not args.no_wait:
        return wait_for_sync(client, timeout=args.timeout, after_id=latest_before)
    return 0


def cmd_status(client: ApiClient, args: argparse.Namespace) -> int:
    health = client.get("/health")
    onboarding = client.get("/api/onboarding/status")
    sync = client.get("/api/sync/status")
    value = {"health": health, "onboarding": onboarding, "sync": sync}
    if args.json:
        print_json(value)
        return 0
    latest = sync.get("latest") or {}
    summary = latest.get("summary") or {}
    print("Backend:       %s" % health.get("status", "unknown"))
    print("Onboarded:     %s" % (onboarding.get("onboarded") and "yes" or "no"))
    print("OpenRouter:    %s" % (health.get("openrouter_configured") and "configured" or "missing key"))
    print("Sync running:  %s" % (sync.get("running") and "yes" or "no"))
    print("Last sync:     %s" % (latest.get("status") or "never"))
    if latest.get("finished_at"):
        print("Finished at:   %s" % latest["finished_at"])
    if summary:
        print("Courses:       %s" % summary.get("courses", 0))
        print("Assignments:   %s" % summary.get("assignments", 0))
        print("Events:        %s" % (
            summary.get("canvas_events", 0) + summary.get("timetable_events", 0) + summary.get("planner_items", 0)
        ))
        print("Announcements: %s" % summary.get("announcements", 0))
    print("Pending AI:    %s" % sync.get("pending_analysis", 0))
    print("AI errors:     %s" % sync.get("analysis_errors", 0))
    return 0


def cmd_sync(client: ApiClient, args: argparse.Namespace) -> int:
    latest_before = (client.get("/api/sync/status").get("latest") or {}).get("id")
    result = client.post("/api/sync")
    print(result.get("message", "Sync requested."))
    if result.get("accepted") and not args.no_wait:
        return wait_for_sync(client, timeout=args.timeout, after_id=latest_before)
    return 0


def wait_for_sync(client: ApiClient, timeout: int = 300, after_id: Optional[str] = None) -> int:
    deadline = time.monotonic() + timeout
    saw_running = False
    while time.monotonic() < deadline:
        status_value = client.get("/api/sync/status")
        latest = status_value.get("latest") or {}
        if after_id and latest.get("id") == after_id:
            time.sleep(0.5)
            continue
        if status_value.get("running") or latest.get("status") == "running":
            saw_running = True
            print("Syncing Quercus...", end="\r", flush=True)
            time.sleep(1)
            continue
        if latest.get("status") == "failed":
            print(" " * 30, end="\r")
            raise CliError("Sync failed: " + (latest.get("error") or "unknown error"))
        if latest.get("status") == "succeeded" and (saw_running or latest.get("trigger") in {"manual", "onboarding"}):
            print(" " * 30, end="\r")
            summary = latest.get("summary") or {}
            print("Sync complete: %(courses)s courses, %(assignments)s assignments, %(announcements)s announcements." % {
                "courses": summary.get("courses", 0),
                "assignments": summary.get("assignments", 0),
                "announcements": summary.get("announcements", 0),
            })
            warnings = summary.get("warnings") or []
            if warnings:
                print("Warnings: %s" % len(warnings))
            return 0
        time.sleep(0.5)
    raise CliError("Timed out waiting for sync; inspect `python -m app.cli status`")


def cmd_courses(client: ApiClient, args: argparse.Namespace) -> int:
    rows = client.get("/api/courses")
    if args.json:
        print_json(rows); return 0
    print_table(rows, [("code", "COURSE", 16), ("name", "NAME", 46), ("term_name", "TERM", 24)])
    return 0


def cmd_assignments(client: ApiClient, args: argparse.Namespace) -> int:
    query = {"include_completed": str(args.all).lower()}
    if args.course:
        query["course_id"] = args.course
    rows = client.get("/api/assignments?" + urllib.parse.urlencode(query))
    if args.json:
        print_json(rows); return 0
    print_table(rows, [("due_at", "DUE", 22), ("kind", "TYPE", 11), ("title", "ASSIGNMENT", 48),
                       ("completed", "DONE", 5)])
    return 0


def cmd_calendar(client: ApiClient, args: argparse.Namespace) -> int:
    start = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    end = start + dt.timedelta(days=args.days)
    query = {"start": start.isoformat().replace("+00:00", "Z"),
             "end": end.isoformat().replace("+00:00", "Z")}
    if args.course:
        query["course_id"] = args.course
    rows = client.get("/api/calendar/events?" + urllib.parse.urlencode(query))
    if args.json:
        print_json(rows); return 0
    print_table(rows, [("start_at", "WHEN", 22), ("kind", "TYPE", 13), ("title", "EVENT", 56)])
    return 0


def cmd_announcements(client: ApiClient, args: argparse.Namespace) -> int:
    query = {"unread_only": str(args.unread).lower()}
    if args.course:
        query["course_id"] = args.course
    rows = client.get("/api/announcements?" + urllib.parse.urlencode(query))
    if args.json:
        print_json(rows); return 0
    print_table(rows, [("posted_at", "POSTED", 22), ("read_state", "STATE", 8), ("title", "ANNOUNCEMENT", 60)])
    return 0


def cmd_reminders(client: ApiClient, args: argparse.Namespace) -> int:
    path = "/api/reminders"
    if args.status:
        path += "?" + urllib.parse.urlencode({"status": args.status})
    rows = client.get(path)
    if args.json:
        print_json(rows); return 0
    print_table(rows, [("notify_at", "NOTIFY", 22), ("status", "STATUS", 10), ("title", "REMINDER", 58)])
    return 0


def cmd_doctor(client: ApiClient, args: argparse.Namespace) -> int:
    checks: List[tuple[str, bool, str]] = []
    health = client.get("/health")
    checks.append(("API reachable", health.get("status") == "ok", client.base_url))
    schema = client.get("/openapi.json")
    checks.append(("OpenAPI schema", len(schema.get("paths", {})) >= 16,
                   "%s endpoints" % len(schema.get("paths", {}))))
    key = client.get("/api/notifications/vapid-public-key").get("publicKey", "")
    checks.append(("Web Push keys", len(key) >= 80, "generated" if key else "missing"))
    onboarding = client.get("/api/onboarding/status")
    profile = onboarding.get("profile") or {}
    checks.append(("Quercus onboarding", bool(onboarding.get("onboarded")),
                   profile.get("name", "not onboarded")))
    checks.append(("OpenRouter key", bool(health.get("openrouter_configured")),
                   "configured" if health.get("openrouter_configured") else "add OPENROUTER_API_KEY to .env"))
    if onboarding.get("onboarded"):
        sync = client.get("/api/sync/status")
        latest = sync.get("latest") or {}
        checks.append(("Latest Quercus sync", latest.get("status") == "succeeded",
                       latest.get("status") or "never run"))
        courses = client.get("/api/courses")
        checks.append(("Course data", bool(courses), "%s courses" % len(courses)))
    for name, passed, detail in checks:
        print("[%s] %-22s %s" % ("PASS" if passed else "TODO", name, detail))
    failures = [name for name, passed, _ in checks if not passed]
    if failures:
        print("\nAction needed: " + ", ".join(failures))
        return 1
    print("\nEverything is working.")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Terminal client for the Quercus calendar backend")
    root.add_argument("--api", default=DEFAULT_API, help="Backend base URL (default: %(default)s)")
    commands = root.add_subparsers(dest="command", required=True)

    onboard = commands.add_parser("onboard", help="Securely connect a Quercus account")
    onboard.add_argument("--token", help=argparse.SUPPRESS)
    onboard.add_argument("--timezone")
    onboard.add_argument("--reminders", help="Comma-separated days, for example 7,3,1")
    onboard.add_argument("--no-wait", action="store_true")
    onboard.add_argument("--timeout", type=int, default=300)
    onboard.set_defaults(handler=cmd_onboard)

    status_command = commands.add_parser("status", help="Show backend and latest sync status")
    status_command.add_argument("--json", action="store_true")
    status_command.set_defaults(handler=cmd_status)

    sync_command = commands.add_parser("sync", help="Refresh Quercus now")
    sync_command.add_argument("--no-wait", action="store_true")
    sync_command.add_argument("--timeout", type=int, default=300)
    sync_command.set_defaults(handler=cmd_sync)

    for name, handler in (("courses", cmd_courses), ("assignments", cmd_assignments),
                          ("calendar", cmd_calendar), ("announcements", cmd_announcements),
                          ("reminders", cmd_reminders)):
        command = commands.add_parser(name, help="List " + name)
        command.add_argument("--json", action="store_true")
        if name in {"assignments", "calendar", "announcements"}:
            command.add_argument("--course", help="Canvas course ID")
        if name == "assignments":
            command.add_argument("--all", action="store_true", help="Include completed assignments")
        if name == "calendar":
            command.add_argument("--days", type=int, default=30, help="Days ahead (default: 30)")
        if name == "announcements":
            command.add_argument("--unread", action="store_true")
        if name == "reminders":
            command.add_argument("--status", choices=["pending", "delivered", "completed", "cancelled"])
        command.set_defaults(handler=handler)

    doctor = commands.add_parser("doctor", help="Run end-to-end configuration checks")
    doctor.set_defaults(handler=cmd_doctor)
    return root


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.handler(ApiClient(args.api), args) or 0)
    except (CliError, KeyboardInterrupt) as exc:
        message = str(exc) if str(exc) else "Cancelled"
        print("Error: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
