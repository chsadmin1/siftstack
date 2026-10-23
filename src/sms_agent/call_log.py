"""smrtPhone call log for the coaching job, read live from smrtPhone.

smrtPhone has no public call-history API and no webhook for a finished call.
Its web app's call log (POST /logs/calls/filtered) does carry everything the
coaching job needs: the rep, direction, duration, disposition, both numbers,
the DataSift record link and a direct recording URL on rec.smrtphone.io that
needs no login. The agent already reads that log for call takeover
(call_takeover.py), through the browser session in SMRTPHONE_STATE_FILE, so it
serves the same rows to the coaching job over GET /hooks/{secret}/calls. One
saved smrtPhone login on the Fly volume covers takeover, the reconcile
backstop and coaching, and a GitHub Actions job can read the log without a
volume mount or flyctl.

The login is uploaded with POST /hooks/{secret}/smrtphone-session (see
`save_session`), from the "Upload smrtPhone session" workflow.

Row shapes (verified live 2026-08-28, see call_takeover._cell):

    created_at  {"date": "2026-08-28 15:30:27.000000", "timezone": "UTC", ...}
    from_num    {"fromNum": "+1865...", "userName": "Adriana - 11", ...}
    to_num      {"toNum": "+1865...", "contactName": "Marticia Queen"}
    user        {"id": 63932, "name": "Adriana Mondragon"}
    recording_sid {"hasRec": "https://rec.smrtphone.io/...", "viewRoute": "/..."}

Read-only, apart from `save_session`.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import call_takeover, config, store

PER_PAGE = 200
# 25 pages x 200 rows covers about 5,000 calls back from now, far more than a
# day of this team's dialing. Paging stops as soon as a page reaches calls
# older than the window, so a normal request reads one or two pages.
MAX_PAGES = 25

_STAMP = "%Y-%m-%d %H:%M:%S"
_OFFSET = re.compile(r"^([+-])(\d{2}):?(\d{2})$")
_URL = re.compile(r"https?://[^\s\"'<>]+")


class SessionError(RuntimeError):
    """The smrtPhone session is missing, expired or unreadable."""


def _zone(label: str):
    label = (label or "").strip()
    if not label or label.upper() in ("UTC", "Z", "GMT"):
        return timezone.utc
    m = _OFFSET.match(label)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))
    try:
        return ZoneInfo(label)
    except Exception:  # noqa: BLE001 - unknown label: treat as UTC, as pull_calls.py does
        return timezone.utc


def created_utc(row: dict) -> str:
    """The call's start as 'YYYY-MM-DD HH:MM:SS' UTC, the coaching job's format."""
    val = row.get("created_at")
    if isinstance(val, dict):
        raw, zone = str(val.get("date") or ""), str(val.get("timezone") or "")
    else:
        raw, zone = call_takeover._clean(val), ""
    raw = raw.strip().replace("T", " ")[:19]
    try:
        dt = datetime.strptime(raw, _STAMP)
    except ValueError:
        return raw
    return dt.replace(tzinfo=_zone(zone)).astimezone(timezone.utc).strftime(_STAMP)


def _recording(row: dict) -> str:
    rec = row.get("recording_sid")
    url = rec.get("hasRec") if isinstance(rec, dict) else None
    return url if isinstance(url, str) and url.startswith("http") else ""


def _e164(digits: str) -> str:
    return f"+1{digits}" if digits else ""


def to_call(row: dict) -> dict | None:
    """One call-log row in the shape src/call_coaching speaks."""
    call_id = call_takeover._clean(row.get("id"))
    if not call_id:
        return None

    direction = call_takeover._clean(row.get("direction")).lower()
    frm = store.clean_phone(call_takeover._cell(row, "from_num", "fromNum", "number"))
    to = store.clean_phone(call_takeover._cell(row, "to_num", "toNum", "number"))
    if direction.startswith("out"):
        customer = to
    elif direction.startswith("in"):
        customer = frm
    else:
        customer = call_takeover._counterparty(row)

    rep = (call_takeover._cell(row, "user", "name")
           or call_takeover._cell(row, "from_num", "userName")
           or "unknown")
    contact = (call_takeover._cell(row, "to_num", "contactName")
               or call_takeover._cell(row, "from_num", "contactName"))

    record_uuid, _owner = call_takeover._crm_ids(row)
    if not record_uuid and customer:
        record_uuid = (store.lookup_phone(customer) or {}).get("record_uuid") or ""
    if contact == rep:
        contact = ""
    if not contact and customer:
        contact = (store.lookup_phone(customer) or {}).get("first_name") or ""
    link = _URL.search(str(row.get("podio_id") or ""))

    return {
        "call_id": call_id,
        "caller": rep,
        "created_at_utc": created_utc(row),
        "direction": direction,
        "status": call_takeover._clean(row.get("status")),
        "disposition": call_takeover._clean(row.get("disposition")),
        "duration_seconds": call_takeover._seconds(row),
        "from_num": _e164(frm),
        "to_num": _e164(to),
        "contact_name": contact,
        "contact_phone": _e164(customer),
        "recording_url": _recording(row),
        "crm_link": link.group(0) if link else "",
        "reisift_record_url": (f"https://app.reisift.io/records/{record_uuid}" if record_uuid else ""),
    }


def _bound(value: str) -> str:
    """A UTC ISO stamp (with or without offset) -> 'YYYY-MM-DD HH:MM:SS' UTC."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(_STAMP)


def calls_between(since_iso: str, until_iso: str, fetch_page=None) -> list[dict]:
    """Every smrtPhone call that started in [since, until), oldest first.

    `fetch_page(page_number) -> rows` is injectable for tests; by default it
    reads the live log through the saved session. Raises SessionError when the
    session is missing or expired, so the caller can say exactly that.
    """
    since, until = _bound(since_iso), _bound(until_iso)
    if fetch_page is None:
        try:
            session = call_takeover._session()
        except Exception as exc:  # noqa: BLE001 - no file, or a file with no cookies
            raise SessionError(str(exc)) from None

        def fetch_page(page: int) -> list[dict]:
            return call_takeover.fetch_page(session, page, PER_PAGE)

    calls: list[dict] = []
    for page in range(MAX_PAGES):
        try:
            rows = fetch_page(page)
        except RuntimeError as exc:
            raise SessionError(str(exc)) from None
        if not rows:
            break
        oldest = None
        for row in rows:
            call = to_call(row)
            if not call:
                continue
            ts = call["created_at_utc"]
            if len(ts) != 19:
                continue
            oldest = ts if oldest is None or ts < oldest else oldest
            if since <= ts < until:
                calls.append(call)
        if oldest is not None and oldest < since:
            break
    calls.sort(key=lambda c: c["created_at_utc"])
    return calls


def save_session(state) -> int:
    """Write an uploaded smrtPhone login to SMRTPHONE_STATE_FILE.

    Accepts Playwright's storage_state (what src/call_coaching/smrtphone_login.py
    saves) or a bare list of cookies. Keeps only smrtPhone's cookies and
    localStorage, writes atomically, and returns how many cookies were kept.
    """
    if isinstance(state, list):
        state = {"cookies": state}
    if not isinstance(state, dict):
        raise ValueError("expected a JSON object with a cookies list")
    cookies = [c for c in state.get("cookies") or []
               if isinstance(c, dict) and "smrt" in str(c.get("domain", "")) and c.get("name")]
    if not cookies:
        raise ValueError("no smrtPhone cookies in the upload")
    origins = [o for o in state.get("origins") or []
               if isinstance(o, dict) and "smrt" in str(o.get("origin", ""))]
    path = Path(config.SMRTPHONE_STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"cookies": cookies, "origins": origins}), encoding="utf-8")
    os.replace(tmp, path)
    return len(cookies)


def check_session() -> tuple[bool, str]:
    """Can the saved login read the call log right now? Reads one row."""
    try:
        rows = call_takeover.fetch_page(call_takeover._session(), 0, 1)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]
    return True, f"call log readable ({len(rows)} row on the first page)"
