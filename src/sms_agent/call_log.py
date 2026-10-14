"""Kixie call log, rebuilt from the end-call webhooks the receiver already stores.

Kixie has no call-history API we can rely on, but every `endcall` webhook
lands in the events table (receiver.kixie_hook -> kixie.normalize_webhook),
and the raw payload rides along under `_kixie` with the recording URL. This
module turns those rows into the call dicts the coaching engine
(src/call_coaching) already speaks, so the coaching side never has to know
which dialer it is reading.

Read-only. Served over GET /hooks/{secret}/calls so a GitHub Actions job can
pull the log without a volume mount or flyctl.

Rep attribution: Kixie's sample payload puts the same person in fname/lname
and calleridName, and its docs are ambiguous about which side each belongs
to. So the rep is resolved, in order, from SMS_AGENT_REP_NUMBERS (a JSON map
of our outbound number -> rep name, the only fully reliable anchor), then
calleridName, then the business number. Verify against the first live
payload and fill the map if calleridName turns out to be the contact.
"""
from __future__ import annotations

import json
import os
from typing import Any

from . import store


def _rep_numbers() -> dict[str, str]:
    raw = os.environ.get("SMS_AGENT_REP_NUMBERS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {store.clean_phone(k): str(v) for k, v in data.items() if store.clean_phone(k)}


def _details(payload: dict) -> tuple[dict, dict]:
    """(callDetails, unwrapped top level) from the raw Kixie payload."""
    raw = payload.get("_kixie") or {}
    top = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    details = top.get("callDetails") if isinstance(top.get("callDetails"), dict) else {}
    merged_top = dict(top)
    for key in ("customernumber", "businessnumber", "number"):
        if key not in merged_top and key in raw:
            merged_top[key] = raw[key]
    return details, merged_top


def _utc_stamp(value: str) -> str:
    """Kixie's ISO calldate -> 'YYYY-MM-DD HH:MM:SS' UTC, the engine's format."""
    v = (value or "").strip().replace("T", " ")
    return v[:19] if len(v) >= 19 else v


def _to_call(row: Any, reps: dict[str, str], dispositions: dict[str, str]) -> dict | None:
    payload = json.loads(row["payload"])
    details, top = _details(payload)
    call_id = str(payload.get("callId") or details.get("callid") or "")
    if not call_id:
        return None

    direction = str(details.get("calltype") or "").lower()
    customer = store.clean_phone(top.get("customernumber") or payload.get("phone"))
    business = store.clean_phone(top.get("businessnumber") or top.get("number"))
    if not business:
        ours = details.get("fromnumber") if direction == "outgoing" else details.get("tonumber")
        business = store.clean_phone(ours)

    rep = (reps.get(business)
           or str(details.get("calleridName") or "").strip()
           or str(payload.get("user") or "").strip()
           or business or "unknown")

    mapped = store.lookup_phone(customer) if customer else None
    contact = " ".join(p for p in (str(details.get("fname") or "").strip(),
                                   str(details.get("lname") or "").strip()) if p)
    if contact == rep:  # fname/lname turned out to be our agent, not the seller
        contact = ""
    if not contact and mapped:
        contact = mapped.get("first_name") or ""
    record_uuid = (mapped or {}).get("record_uuid") or ""

    disposition = (dispositions.get(call_id)
                   or str(details.get("disposition") or payload.get("disposition") or ""))
    if disposition.lower() == "not disposed":
        disposition = ""

    return {
        "call_id": call_id,
        "caller": rep,
        "created_at_utc": _utc_stamp(str(details.get("calldate") or "")) or _utc_stamp(row["received_at"]),
        "direction": direction,
        "status": str(details.get("callstatus") or payload.get("status") or ""),
        "disposition": disposition,
        "duration_seconds": int(payload.get("duration") or 0),
        "from_num": f"+1{business}" if direction == "outgoing" and business else (f"+1{customer}" if customer else ""),
        "to_num": f"+1{customer}" if direction == "outgoing" and customer else (f"+1{business}" if business else ""),
        "contact_name": contact,
        "contact_phone": f"+1{customer}" if customer else "",
        "recording_url": str(details.get("recordingurl") or ""),
        "crm_link": str(details.get("crmlink") or ""),
        "reisift_record_url": (f"https://app.reisift.io/records/{record_uuid}" if record_uuid else ""),
    }


def calls_between(since_iso: str, until_iso: str) -> list[dict]:
    """Every Kixie call whose webhook arrived in [since, until), oldest first.

    Bounds are compared against received_at (UTC ISO), which trails the call's
    end by seconds, so a caller asking for a local business day should pad the
    window and filter on created_at_utc itself.
    """
    c = store._conn()
    disp_rows = c.execute(
        "SELECT payload FROM events WHERE source='kixie' AND event_type='kixie:disposition'"
        " AND received_at >= ? AND received_at < ?",
        (since_iso, until_iso),
    ).fetchall()
    dispositions: dict[str, str] = {}
    for r in disp_rows:
        p = json.loads(r["payload"])
        if p.get("callId") and p.get("disposition"):
            dispositions[str(p["callId"])] = str(p["disposition"])

    rows = c.execute(
        "SELECT payload, received_at FROM events WHERE source='kixie' AND event_type='callEnded'"
        " AND received_at >= ? AND received_at < ? ORDER BY received_at",
        (since_iso, until_iso),
    ).fetchall()
    reps = _rep_numbers()
    calls = []
    for r in rows:
        call = _to_call(r, reps, dispositions)
        if call:
            calls.append(call)
    return calls
