"""Safety net: scan the connected mailbox for opt-out replies and suppress
them, on both channels.

v1 is deliberately one-way (no reply classification, no drafted responses,
see the module docstring in `__init__.py`). But an unsubscribe reply is not
"a conversation to have", it is a request this codebase already treats as
absolute on the SMS side ("Opt-outs are decided by regex, never by a model" -
sms_agent/README.md). Running that same narrow, deterministic check against
the mailbox is a guardrail, not the two-way engine the user explicitly chose
not to build yet.

Suppressing locally AND tagging the CRM record "Do Not Market" (the same tag
`sms_agent.escalate` writes) is what makes this cross-channel: a person who
unsubscribes from email is excluded from the next SMS touch too, and vice
versa, because both sides check the same tag.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))
from sms_agent import crm as sms_crm  # noqa: E402

from . import config, mailer, store

log = logging.getLogger(__name__)

# Deliberately narrow and literal, same posture as the SMS opt-out regex:
# false negatives (missing a subtler opt-out) are far safer than false
# positives (suppressing someone who did not ask to be).
_OPT_OUT = re.compile(
    r"\b(unsubscribe|remove me|take me off|stop emailing|stop contacting|"
    r"do not email|don'?t email me|no longer interested.*(remove|list)|"
    r"opt out|stop e-?mailing)\b",
    re.I,
)


def scan(limit: int = 100, commit: bool = False) -> dict:
    """Read the last `limit` mailbox messages, suppress any opt-out sender.

    Read-only against the mailbox either way (`read-email` never writes).
    `commit` gates the two things this DOES write: the local suppression row
    and the CRM tag, same DRY_RUN-style posture as every write elsewhere in
    this codebase.
    """
    mailbox = mailer.resolve_mailbox()
    if not mailbox:
        return {"error": "no connected mailbox found; run cli.py doctor"}
    integration_id = mailbox.get("id") or mailbox.get("uuid")

    rows = mailer.read_email(str(integration_id), limit=limit)
    checked = 0
    hits = []
    for msg in rows:
        checked += 1
        body = " ".join(str(msg.get(k) or "") for k in ("body", "text", "snippet", "subject"))
        sender = store.clean_email(msg.get("from") or msg.get("from_email") or msg.get("sender"))
        if not sender or not _OPT_OUT.search(body):
            continue
        hits.append(sender)
        if not commit:
            continue
        store.suppress(sender, "opt_out")
        for row in _find_records_by_email(sender):
            sms_crm.add_tags(row, [config.TAG_OPT_OUT])

    return {"checked": checked, "opt_outs": sorted(set(hits)), "committed": commit}


def _find_records_by_email(email: str) -> list:
    """Every record uuid carrying this address, verified the same way the SMS
    side verifies a phone hit (fuzzy search, then confirm on the owner)."""
    c = sms_crm.client()
    if not c:
        return []
    try:
        resp = c._request(
            "/api/internal/property/", method="POST",
            body={"limit": 10, "query": {"must": {"search": email}}},
            method_override="GET",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("email lookup failed for %s: %s", email, exc)
        return []
    out = []
    for row in resp.get("results") or []:
        uuid = row.get("uuid")
        if not uuid:
            continue
        full = sms_crm.get_record(uuid) or {}
        owner = full.get("owner") if isinstance(full.get("owner"), dict) else {}
        addrs = {
            store.clean_email(e if isinstance(e, str) else (e or {}).get("email") or (e or {}).get("address"))
            for e in owner.get("emails") or []
        }
        if email in addrs:
            out.append(uuid)
    return out
