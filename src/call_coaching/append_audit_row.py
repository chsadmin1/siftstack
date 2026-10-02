"""append_audit_row.py - append graded call rows to the Daily Call Audit Google Sheet.

Target sheet (fixed, one continuous log, never a new tab):
  https://docs.google.com/spreadsheets/d/16smYV-EXVzN4dN4sQEGbLBvTbdoxwCheUaqe5Hoao2k/edit
  tab "Sheet1" (verified live 2026-08-04).

Reads the ACTUAL header row from the sheet at run time and maps each input
row by header name - so wherever the extra columns (Date reviewed, Number
Dialed, Name of the contact) end up sitting relative to the original five,
this script still lands each value in the right place. Unknown headers are
left blank; input keys with no matching header are reported, not silently
dropped.

Auth: reuses the project's existing Google service account
(GOOGLE_SERVICE_ACCOUNT_KEY, base64-encoded JSON, in .env - same account
sheets_uploader.py uses). That service account MUST be shared as Editor on
this specific sheet first (Share -> paste its client_email -> Editor) -
read access alone (e.g. via a public link) is not enough to append rows.

USAGE (from SiftStack root, venv python):
  python src/call_coaching/append_audit_row.py --json output/call_coaching/daily_audit_rows.json

Input JSON: a list of objects, each keyed by the sheet's own header text, e.g.:
  [{"Sales Rep": "Tinaa George", "Outbound phone number": "+18165551234",
    "Call summary": "...", "What the rep did well": "...",
    "Areas for improvement": "...", "Date reviewed": "2026-08-03",
    "Number Dialed": "+18165559999", "Name of the contact": "Jane Doe"}, ...]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPREADSHEET_ID = "16smYV-EXVzN4dN4sQEGbLBvTbdoxwCheUaqe5Hoao2k"
TAB_NAME = "Sheet1"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _env(key: str) -> str:
    txt = (ROOT / ".env").read_text(encoding="utf-8")
    m = re.search(rf"^{key}\s*=\s*(\S+)", txt, re.MULTILINE)
    if not m:
        raise KeyError(f"{key} not set in .env")
    return m.group(1).strip()


def _range_url(updated_range: str) -> str:
    """Deep-link straight to the rows just appended (Sheets opens scrolled to
    and with this range selected) rather than just the sheet's front page."""
    cell_range = updated_range.split("!", 1)[-1]
    return f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid=0&range={cell_range}"


def _notify_slack(range_url: str) -> None:
    """Post to #2-deals-a-week that today's batch is ready. Best-effort - a
    missing webhook or a failed post never fails the append itself, since the
    rows are already safely in the sheet by the time this runs."""
    try:
        webhook = _env("SLACK_CALL_AUDIT_WEBHOOK_URL")
    except KeyError:
        print("WARNING: SLACK_CALL_AUDIT_WEBHOOK_URL not set in .env, skipping Slack notification",
              file=sys.stderr)
        return

    sys.path.insert(0, str(ROOT / "src"))
    from slack_notifier import _send_webhook

    text = f"Yesterday's call reviews are ready here - {range_url}"
    if not _send_webhook(text, webhook):
        print("WARNING: Slack notification failed to send", file=sys.stderr)


def _build_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    key_json = json.loads(base64.b64decode(_env("GOOGLE_SERVICE_ACCOUNT_KEY")))
    creds = Credentials.from_service_account_info(key_json, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def get_headers(svc) -> list[str]:
    resp = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"'{TAB_NAME}'!1:1"
    ).execute()
    rows = resp.get("values", [])
    return rows[0] if rows else []


def build_rows(records: list[dict], headers: list[str]) -> list[list[str]]:
    # Match header <-> input keys with whitespace stripped (sheet headers can pick
    # up stray leading/trailing spaces from typing/pasting) without touching the
    # sheet itself; values are still written under the header's real column.
    norm_headers = [h.strip() for h in headers]
    known = set(norm_headers)
    unknown_keys = set()
    for rec in records:
        unknown_keys.update(k.strip() for k in rec if k.strip() not in known)
    if unknown_keys:
        print(f"WARNING: these input keys have no matching sheet column and will be dropped: "
              f"{sorted(unknown_keys)}", file=sys.stderr)

    rows = []
    for rec in records:
        norm_rec = {k.strip(): v for k, v in rec.items()}
        rows.append([str(norm_rec.get(h, "")) for h in norm_headers])
    return rows


def append(records: list[dict]) -> dict:
    if not records:
        return {"success": True, "appended": 0}

    svc = _build_service()
    headers = get_headers(svc)
    if not headers:
        return {"success": False, "error": f"could not read header row from '{TAB_NAME}'!1:1"}

    rows = build_rows(records, headers)

    try:
        result = svc.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{TAB_NAME}'!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "PERMISSION_DENIED" in msg or "403" in msg:
            msg += ("\nMake sure the service account is shared as Editor on the sheet "
                    "(Share -> paste its client_email from GOOGLE_SERVICE_ACCOUNT_KEY -> Editor).")
        return {"success": False, "error": msg}

    sheet_url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"
    updates = result.get("updates", {})
    updated_range = updates.get("updatedRange")
    if updated_range:
        _notify_slack(_range_url(updated_range))

    return {"success": True, "appended": len(rows), "sheet_url": sheet_url, "updates": updates}


def main() -> int:
    ap = argparse.ArgumentParser(description="Append graded call rows to the audit sheet")
    ap.add_argument("--json", required=True, help="path to a JSON list of row objects")
    args = ap.parse_args()

    records = json.loads(Path(args.json).read_text(encoding="utf-8"))
    if isinstance(records, dict):
        records = [records]

    result = append(records)
    print(json.dumps(result, indent=1))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
