"""Build one day's email cadence run. Sends nothing by itself.

Mirrors `sms_agent.campaign`'s shape: pull the board, work out who is due
their NEXT touch from their own send history (not from where they sit in any
call-attempt stage), render it, validate it, hold back anyone who fails a
guard. `cli.py` is what actually calls `mailer.send_email` on a `ready`
candidate, gated on --commit the same way every write in this codebase is.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))
from sms_agent import crm as sms_crm  # noqa: E402

from . import config, store, validate
from .knowledge import touches

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    email: str
    record_uuid: str = ""
    first: str = ""
    owner_full: str = ""
    street: str = ""
    city: str = ""
    sender: str = ""
    subject: str = ""
    body: str = ""
    status: str = "ready"
    touch: int = 0
    reasons: list = field(default_factory=list)

    def hold(self, reason: str) -> "Candidate":
        self.status = "hold"
        self.reasons.append(reason)
        return self


def _record_tags(rec: dict) -> set:
    return {
        (t.get("name") or t.get("title") if isinstance(t, dict) else str(t))
        for t in (rec.get("tags") or [])
    }


def _owner_emails(owner: dict) -> list:
    """Every usable address on the owner, in the order Sift lists them.

    Defensive about shape: an email entry could be a bare string or a dict
    carrying {email|address, status|type}. A dict explicitly flagged invalid,
    bounced or opted out is skipped rather than trusted.
    """
    out = []
    for e in owner.get("emails") or []:
        if isinstance(e, str):
            addr = e
            bad = False
        elif isinstance(e, dict):
            addr = e.get("email") or e.get("address") or ""
            status = str(e.get("status") or "").upper()
            bad = status in ("INVALID", "BOUNCED", "OPT_OUT", "OPTED_OUT", "DNC")
        else:
            continue
        cleaned = store.clean_email(addr)
        if cleaned and not bad and cleaned not in out:
            out.append(cleaned)
    return out


def footer(sender: str) -> str:
    """CAN-SPAM footer: physical address + a plain-text opt-out line.

    No link on purpose, same reasoning the SMS side has for never including
    one: a link in a cold outreach message reads as a mail-merge and hurts
    deliverability as much as it hurts the human read. "Reply and let me know"
    satisfies the opt-out requirement without one.
    """
    addr = config.PHYSICAL_ADDRESS
    lines = ["", "---", addr]
    lines.append(f"If you'd rather not hear from {sender} again, just reply and say so and you won't.")
    return "\n".join(lines)


def from_preset(title: str, limit: int = 0) -> tuple[list, str]:
    """Every record in the preset that has SOME email, live-fetched.

    Unlike the SMS side's `from_preset`, which reads email/phone off the slim
    search payload, owner emails are not documented as present there, so this
    fetches the full record per row. That costs one call per candidate instead
    of zero, acceptable at this board's size (a few hundred, not thousands);
    revisit if the email cadence ever needs to run at bulk-list scale.
    """
    must, matched = sms_crm.resolve_preset(title)
    if not must:
        return [], ""
    rows = []
    for slim in sms_crm.fetch_cohort(must, limit=limit):
        uuid = slim.get("uuid")
        if not uuid:
            continue
        rec = sms_crm.get_record(uuid)
        if not rec:
            continue
        rows.append(rec)
    return rows, matched


def build(records, touch: int, sender_fallback: str = "", render_fn: Optional[Callable] = None,
          gate: bool = True) -> list:
    """Turn full CRM records into vetted, rendered candidates. Sends nothing.

    `render_fn`, when given, replaces `touches.render(touch, ...)` as the copy
    source - same signature, `(seed, first, street, city, sender) -> (subject,
    body)`. `gate=False` skips the `_next_touch` "is this address due for
    THIS touch, on its own send history" check below, so `touch` becomes a
    plain label rather than a position in the 1-3 progression. Both exist for
    the "DxD Exhausted" farewell cadence, which schedules by calendar day
    since the record entered that column, not by "next touch not yet sent" -
    a different rule than this function's default one, not a variation of it.
    Every other guard (suppression, opt-out tag, paused thread, dedupe within
    the run, name hygiene, sender resolution, the content validator, the
    CAN-SPAM footer) stays exactly as it is for the numbered touches.
    """
    out: list = []
    already_this_run: set = set()

    for rec in records:
        addr = rec.get("address") or {}
        owner = rec.get("owner") or {}
        record_uuid = rec.get("uuid") or ""
        owner_full = owner.get("company") or " ".join(
            x for x in (owner.get("first_name"), owner.get("last_name")) if x
        )
        street = addr.get("street") or ""

        cand = Candidate(email="", record_uuid=record_uuid, street=street,
                          city=addr.get("city") or "", owner_full=owner_full)

        if not street:
            out.append(cand.hold("no street address"))
            continue

        emails = _owner_emails(owner)
        if not emails:
            out.append(cand.hold("no usable email on the owner"))
            continue
        cand.email = emails[0]

        tags = _record_tags(rec)
        if config.TAG_OPT_OUT in tags:
            out.append(cand.hold(f"tagged {config.TAG_OPT_OUT!r}"))
            continue
        if any(str(t).lower().startswith(f"{config.TAG_PREFIX}ai_paused") for t in tags):
            out.append(cand.hold("thread paused (human took over on another channel)"))
            continue

        reason = store.is_suppressed(cand.email)
        if reason:
            out.append(cand.hold(f"suppressed ({reason})"))
            continue

        if cand.email in already_this_run:
            out.append(cand.hold("same address already has a touch in this batch"))
            continue

        if gate:
            hist = store.prior_touches(cand.email)
            due, why = _next_touch(hist, config.TOUCH_GAP_DAYS, datetime.now(timezone.utc).date())
            if due is None:
                out.append(cand.hold(why))
                continue
            if due != touch:
                out.append(cand.hold(f"due for touch {due}, this run is building touch {touch}"))
                continue

        if touches.is_entity(owner_full):
            cand.first = ""
        elif owner.get("first_name"):
            cand.first = touches.clean_first(owner["first_name"])
        else:
            cand.first = touches.clean_first(owner_full)

        assigned = rec.get("assigned_to")
        if isinstance(assigned, dict):
            assigned = assigned.get("uuid") or assigned.get("id") or ""
        cand.sender = sms_crm.sender_name_for(assigned or "") or sender_fallback
        if not cand.sender:
            out.append(cand.hold("no assigned caller name; an email is signed or it is not sent"))
            continue

        renderer = render_fn or (lambda **kw: touches.render(touch, **kw))
        subject, body = renderer(
            seed=f"{street}|{owner_full}".lower(),
            first=cand.first, street=street, city=cand.city, sender=cand.sender,
        )
        ok, problems = validate.validate(body, max_questions=1)
        if not ok:
            out.append(cand.hold("copy failed the content check: " + "; ".join(problems)))
            continue

        cand.subject = subject
        cand.body = body + footer(cand.sender)
        cand.touch = touch
        already_this_run.add(cand.email)
        out.append(cand)

    return out


def plan(preset: str = "", limit: int = 0, sender_fallback: str = "") -> list:
    """One day's full batch: everyone in the preset advanced to whichever of
    the three touches THEY are due for, fetched once and matched against each
    touch in turn.

    A given email can only be `ready` out of ONE of the three calls below,
    because `_next_touch` is a function of that email's own history, not of
    which record triggered the check: whichever touch their history says is
    due is the only one `build()` will not hold back for "wrong touch".
    """
    records, matched = from_preset(preset or config.CAMPAIGN_PRESET, limit=limit)
    if not matched:
        return []
    ready: list = []
    for touch in (1, 2, 3):
        ready.extend(c for c in build(records, touch, sender_fallback) if c.status == "ready")
    return ready


def _next_touch(hist: dict, min_days: int, today: date) -> tuple:
    had = hist.get("touches") or set()
    if not had:
        return 1, ""
    done = max(had)
    if done >= 3:
        return None, "completed all three touches"
    last = hist.get("last") or ""
    if last:
        try:
            when = datetime.fromisoformat(last.replace("Z", "+00:00")).date()
        except ValueError:
            when = None
        if when and (today - when).days < min_days:
            waited = (today - when).days
            return None, f"touch {done} was {waited}d ago, waiting {min_days}d"
    return done + 1, ""


def summary(candidates: list) -> dict:
    ready = [c for c in candidates if c.status == "ready"]
    reasons: dict = {}
    for cand in candidates:
        for r in cand.reasons:
            key = r.split(":")[0].split("(")[0].strip()
            reasons[key] = reasons.get(key, 0) + 1
    return {
        "total": len(candidates),
        "ready": len(ready),
        "held": len(candidates) - len(ready),
        "held_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
    }
