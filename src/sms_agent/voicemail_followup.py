"""End-of-day voicemail follow-up: "sorry I missed you" texts.

Every text touch elsewhere in this package is either a scheduled campaign
touch (`campaign.py`) or a reply to something the owner sent (`engine.py`).
This is neither: it is triggered by something WE did on the phone - Don or
Nico dialed a number today, left a voicemail, and never got a live answer.

The source is smrtPhone's own call log (`/logs/calls/filtered`), the same
DataTables endpoint `src/call_coaching/pull_calls.py` already pulls from and
`reconcile.py` already authenticates against for the SMS log - reused here
rather than re-implemented. It carries `disposition`, `direction`, `to_num`
and the caller's name, which is everything this needs and more than the
reisift activity log exposes (that log's `owner.call.*` events distinguish
answered/no-answer, not "a voicemail was left").

The cohort is deliberately narrowed to whoever is CURRENTLY sitting in the
campaign preset (config.CAMPAIGN_PRESET, "D x D") - a voicemail left for a
number that has since left the board (sold, DNC'd, reassigned off the
account) should not get a text.

Nothing here trusts a guessed disposition string blindly: `probe()` prints
the actual (direction, disposition) pairs seen today so the marker list in
config.VM_DISPOSITION_MARKERS can be verified against real data before this
is ever turned on, the same "check one record before trusting it" reflex
this codebase uses everywhere else.

    python src/sms_agent/cli.py vm-probe                # what dispositions look like today
    python src/sms_agent/cli.py vm-followup              # preview, sends nothing
    python src/sms_agent/cli.py vm-followup --queue       # actually stage + queue the texts
"""
from __future__ import annotations

import html
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import config, crm, respond, sender_pool, store

log = logging.getLogger(__name__)

BASE = "https://phone.smrt.studio"
CALL_LOG_PATH = "/logs/calls/filtered"
# Identical shape to src/call_coaching/pull_calls.py's COLUMNS - verified live
# against this endpoint already, reused rather than re-derived.
CALL_COLUMNS = [
    "id", "user", "user_id", "created_at", "direction", "status",
    "disposition", "from_num", "to_num", "price", "duration",
    "podio_id", "recording_sid", "sid", "call_agent_id",
]

# A handful of phrasings so ten voicemail follow-ups sent the same afternoon
# do not all read as one copy-pasted line. Deliberately plain: no question, no
# apology-for-being-random (that is touch-1's job, not a callback's).
POOL = [
    "This is {sender}. I tried calling you today about your house at {addr}, but wasn't able to get you on the phone. I'll try again in the next day or two.",
    "Hi, this is {sender}. I called earlier today about {addr} and missed you. I'll try again in the next day or two.",
    "{sender} here. I called about your house at {addr} today but couldn't get through. I'll give it another shot in the next day or two.",
]


def render(seed: str, sender: str, addr: str) -> str:
    n = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    template = POOL[n % len(POOL)]
    return re.sub(r"\s+", " ", template.format(sender=sender, addr=addr)).strip()


def _session():
    from . import numbers_sync

    return numbers_sync._session()


def _clean(value) -> str:
    """Log cells are HTML fragments; some are entity-encoded (matches reconcile._clean)."""
    text = html.unescape(str(value or ""))
    return re.sub(r"<[^>]+>", "", text).strip()


def fetch_calls(pages: int = 4, per_page: int = 200) -> list[dict]:
    """Recent call log rows, newest first."""
    session = _session()
    rows: list[dict] = []
    for page in range(pages):
        form = {
            "draw": "1", "start": str(page * per_page), "length": str(per_page),
            "order[0][column]": "3", "order[0][dir]": "desc",
            "search[value]": "", "search[regex]": "false",
        }
        for i, col in enumerate(CALL_COLUMNS):
            form[f"columns[{i}][data]"] = col
            form[f"columns[{i}][name]"] = col
            form[f"columns[{i}][searchable]"] = "true"
            form[f"columns[{i}][orderable]"] = "true"
            form[f"columns[{i}][search][value]"] = ""
            form[f"columns[{i}][search][regex]"] = "false"
        resp = session.post(BASE + CALL_LOG_PATH, data=form, timeout=90)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} from the call log")
        try:
            page_rows = resp.json().get("data") or []
        except ValueError:
            raise RuntimeError("session expired; re-run _api/smrtphone_login.py") from None
        if not page_rows:
            break
        rows.extend(page_rows)
    return rows


def _created_at(row: dict) -> Optional[datetime]:
    """Naive UTC, same assumption siftline_kpi.py makes about this account's logs."""
    raw = row.get("created_at")
    text = _clean(raw.get("date") if isinstance(raw, dict) else raw)
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _to_number(row: dict) -> str:
    raw = row.get("to_num")
    number = raw.get("toNum") if isinstance(raw, dict) else raw
    return store.clean_phone(number)


def _recent_outbound(hours: int) -> list[dict]:
    """Outbound call rows from the last `hours`, newest first, oldest cut off."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = fetch_calls(pages=4)
    out = []
    for row in rows:
        direction = _clean(row.get("direction")).lower()
        if direction not in ("outbound", "out"):
            continue
        dt = _created_at(row)
        if dt is None or dt < cutoff:
            continue
        out.append(row)
    return out


def probe(hours: int = 18) -> dict:
    """(direction, disposition) counts from today's calls. Verify before trusting the filter.

    A guessed marker list that never matches this account's real disposition
    strings would make the whole feature a silent no-op, which is worse than
    not building it: nobody would notice until asking why nothing sent.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    counts: dict[tuple[str, str], int] = {}
    for row in fetch_calls(pages=4):
        dt = _created_at(row)
        if dt is None or dt < cutoff:
            continue
        key = (_clean(row.get("direction")) or "(blank)", _clean(row.get("disposition")) or "(blank)")
        counts[key] = counts.get(key, 0) + 1
    return counts


def is_voicemail(disposition: str) -> bool:
    d = (disposition or "").strip().lower()
    return any(marker in d for marker in config.VM_DISPOSITION_MARKERS)


def _cohort() -> dict[str, dict]:
    """phone (10 digits) -> {record_uuid, street, city} for the campaign preset, right now.

    A voicemail left for a number that has since left the board - sold, gone
    DNC, reassigned off the account - should not earn a follow-up text, so
    this is read fresh each run rather than cached from the morning build.
    """
    must, matched = crm.resolve_preset(config.CAMPAIGN_PRESET)
    if not must:
        return {}
    out: dict[str, dict] = {}
    for rec in crm.fetch_cohort(must, limit=0):
        phone = rec.get("phone") if isinstance(rec.get("phone"), dict) else {}
        digits = store.clean_phone(phone.get("number"))
        if len(digits) != 10:
            continue
        addr = rec.get("address") or {}
        out[digits] = {
            "record_uuid": rec.get("uuid") or "",
            "street": addr.get("street") or "",
            "city": addr.get("city") or "",
        }
    return out


def run(dry_run: bool = False) -> dict:
    """Text everyone left a voicemail today in the campaign preset. Once per person per day."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        rows = _recent_outbound(config.VM_FOLLOWUP_LOOKBACK_HOURS)
    except Exception as exc:  # noqa: BLE001 - an expired session is the usual cause
        log.warning("voicemail follow-up: could not read the call log: %s", exc)
        return {"error": str(exc)[:200]}

    cohort = _cohort()
    if not cohort:
        return {"error": f"preset {config.CAMPAIGN_PRESET!r} not found or empty"}

    seen_phone: set = set()
    queued = 0
    reasons: dict[str, int] = {}

    def hold(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    for row in rows:
        if not is_voicemail(_clean(row.get("disposition"))):
            continue

        phone = _to_number(row)
        if len(phone) != 10 or phone in seen_phone:
            continue

        info = cohort.get(phone)
        if not info:
            hold("not in today's D x D cohort")
            continue

        reason = store.is_suppressed(phone)
        if reason:
            hold(f"suppressed ({reason})")
            continue

        # Claims the (phone, day) slot the first time it is seen. A second
        # voicemail logged for the same person later today must not earn a
        # second "sorry I missed you" text.
        if not store.mark_notified(f"{phone}-{today}", "vm_followup_sent"):
            hold("already followed up today")
            continue

        conv = store.get_conversation(phone)
        if conv and conv.get("state") not in ("active", None, ""):
            hold(f"conversation is {conv['state']}")
            continue
        if conv and conv.get("last_inbound"):
            hold("already replied; a voicemail follow-up would interrupt a live thread")
            continue

        # Prefer the record's ASSIGNED caller over whoever happened to dial it.
        # The two do not always match: a shared dialer roster can carry callers
        # (Wendy, in this account's case) who were deliberately left out of the
        # SMS roster entirely - no number pool, no Slack mapping. Signing with
        # a name that has no pool would fall back to sender_pool's shared-pool
        # rescue and send from someone ELSE's number while claiming to be her,
        # which is exactly the mismatched-identity failure this whole system is
        # built to avoid. The raw caller name is only trusted as a fallback, and
        # only when it resolves to an actually configured pool.
        record_uuid = info.get("record_uuid") or ""
        assigned_name = crm.deal_context(record_uuid).get("assigned_name", "") if record_uuid else ""
        caller_raw = (row.get("user") or {}).get("name") if isinstance(row.get("user"), dict) else ""
        dialer_name = _clean(caller_raw).split()[0] if _clean(caller_raw) else ""
        known_pools = {name.strip().lower() for name in config.number_pools() if name}
        caller = assigned_name or (dialer_name if dialer_name.strip().lower() in known_pools else "")
        if not caller:
            hold(
                "no assigned caller on the record, and the dialer "
                f"({dialer_name or 'unknown'}) has no configured number pool"
            )
            continue

        street = info.get("street") or ""
        if not street:
            hold("no street address on record")
            continue

        message = render(seed=f"{phone}|{today}", sender=caller, addr=street)
        ok, problems = respond.validate(message, max_questions=0)
        if not ok:
            hold("failed the human-voice check: " + "; ".join(problems))
            continue

        seen_phone.add(phone)
        if dry_run:
            queued += 1
            continue

        ctx = {k: v for k, v in {
            "street": street, "city": info.get("city", ""), "assigned_name": caller,
        }.items() if v}
        store.map_phone(phone, record_uuid=record_uuid, address=street, context=ctx or None)

        from_number = sender_pool.assign(phone, caller)
        if not from_number:
            hold(f"no number pool for {caller}")
            continue
        store.ensure_conversation(phone, from_number=from_number, record_uuid=record_uuid)
        store.queue_message(
            phone, message, from_number=from_number, status="queued",
            intent="voicemail_followup", confidence=1.0,
        )
        queued += 1

    return {
        "queued": queued,
        "skipped": sum(reasons.values()),
        "skipped_reasons": reasons,
        "calls_scanned": len(rows),
    }
