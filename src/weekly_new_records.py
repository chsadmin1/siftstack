"""Weekly check: how many new property records landed in the account since the
last run, packaged into a dated DataSift List, with a Slack summary posted.

Uses the account tied to REISIFT_API_KEY (.env). Discovered live 2026-08-26:
the Records page's "Created Date" filter compiles to a `created: [since, until]`
range on `query.must`, and the same range can be handed straight to the
account-wide bulk endpoint `POST /api/internal/property/add-lists/` to tag
every matching record into a new list in one call (no per-record looping).

    python src/weekly_new_records.py              # real run: list + Slack + state update
    python src/weekly_new_records.py --dry-run     # count only, no writes, no Slack
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv

load_dotenv()

from sms_agent.crm_standalone import StandaloneCRM  # noqa: E402
from slack_notifier import _send_webhook  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("weekly_new_records")

STATE_FILE = Path(__file__).parent.parent / "output" / "weekly_new_records_state.json"
DEFAULT_WINDOW_DAYS = 7
POLL_ATTEMPTS = 10
POLL_INTERVAL_SEC = 10


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _count(crm: StandaloneCRM, must: dict) -> int:
    resp = crm._request(
        "/api/internal/property/",
        method="POST",
        method_override="GET",
        body={"limit": 1, "offset": 0, "query": {"must": must}},
    )
    return resp.get("count", 0)


def _list_properties_count(crm: StandaloneCRM, list_uuid: str) -> int:
    resp = crm._request(f"/api/internal/list/{list_uuid}/properties-count/")
    return resp.get("count", 0) if isinstance(resp, dict) else 0


def _find_list_uuid(crm: StandaloneCRM, title: str) -> str | None:
    resp = crm._request("/api/internal/list/", params={"limit": 999})
    results = resp.get("results") or resp.get("data") or []
    for row in results:
        if (row.get("title") or "") == title:
            return row.get("uuid")
    return None


def run(commit: bool = True, window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    api_key = os.environ.get("REISIFT_API_KEY", "")
    if not api_key:
        raise SystemExit("REISIFT_API_KEY not set")
    crm = StandaloneCRM(api_key)

    state = _load_state()
    now = datetime.now(timezone.utc)
    since_dt = None
    if state.get("last_run_iso"):
        try:
            since_dt = datetime.fromisoformat(state["last_run_iso"].replace("Z", "+00:00"))
        except ValueError:
            since_dt = None
    if since_dt is None:
        since_dt = now - timedelta(days=window_days)
    since = _iso(since_dt)

    clean_count = _count(crm, {"created": [since, None], "property_type": "clean"})
    incomplete_count = _count(crm, {"created": [since, None], "property_type": "incomplete"})
    total = clean_count + incomplete_count
    log.info(
        "New records since %s: clean=%d incomplete=%d total=%d",
        since, clean_count, incomplete_count, total,
    )

    list_name = f"New Records - Week of {now.strftime('%m-%d-%Y')}"
    list_uuid = None
    verified_count = None

    if commit and clean_count:
        crm._request(
            "/api/internal/property/add-lists/",
            method="POST",
            body={
                "query": {"must": {"created": [since, None], "property_type": "clean"}},
                "lists": [list_name],
            },
        )
        # add-lists runs as an async background job -- poll the list's own
        # properties-count until it stops climbing (or reaches the expected total).
        for _ in range(POLL_ATTEMPTS):
            time.sleep(POLL_INTERVAL_SEC)
            if not list_uuid:
                list_uuid = _find_list_uuid(crm, list_name)
                if not list_uuid:
                    continue
            verified_count = _list_properties_count(crm, list_uuid)
            if verified_count >= clean_count:
                break

    if commit:
        state["last_run_iso"] = _iso(now)
        _save_state(state)

    result = {
        "since": since,
        "clean_count": clean_count,
        "incomplete_count": incomplete_count,
        "total": total,
        "list_name": list_name if commit and clean_count else None,
        "list_uuid": list_uuid,
        "verified_count": verified_count,
    }

    if commit:
        _notify_slack(result)

    return result


def _notify_slack(result: dict) -> None:
    since_date = result["since"][:10]
    if not result["clean_count"] and not result["incomplete_count"]:
        text = f":zzz: No new records landed this week (since {since_date})."
    else:
        lines = [
            f":inbox_tray: *{result['total']} new records* landed since {since_date}",
            f"  - Clean: {result['clean_count']}",
            f"  - Incomplete: {result['incomplete_count']}",
        ]
        if result["list_name"]:
            verified = result["verified_count"]
            note = f" ({verified} confirmed)" if verified is not None else " (still processing, check back shortly)"
            lines.append(
                f'*List:* "{result["list_name"]}"{note} — DataSift > Lists (search "New Records")'
            )
        text = "\n".join(lines)
    sent = _send_webhook(text)
    if not sent:
        log.warning("Slack notification failed to send")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="count only -- no list created, no Slack post, no state update",
    )
    parser.add_argument(
        "--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
        help="fallback window when no prior run state exists (default 7)",
    )
    args = parser.parse_args()
    res = run(commit=not args.dry_run, window_days=args.window_days)
    print(json.dumps(res, indent=2))
