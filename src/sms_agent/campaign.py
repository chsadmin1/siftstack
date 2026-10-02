"""The daily follow-up: one text per person per day, next in their sequence.

Everybody in the prospector's assigned book walks the same four touches, one a
day, until the sequence is done: identity check, resend, soft ask, goodbye.

Which touch someone gets comes from THEIR OWN text history, not from where the
record sits in the calling cadence. The first build mapped each call-attempt
stage to a fixed touch, which quietly capped almost everyone at touch 1: a
record parked in Ready to Call never advances a stage on its own, so it never
earned touch 2 and the campaign looked exhausted after a single day.

Two things this has to get right, and both are about not annoying people:

  * **Never resend a touch someone already had.** Adriana and Tinaa have been
    sending these same four touches BY HAND out of the smrtPhone inbox, and
    1,600+ numbers already have one. Sending touch 1 to somebody who got it
    last week from a different number is the fastest way to look like a spam
    farm to a human, which matters more than looking like one to a carrier.
  * **One person, one text per run**, even when they own property in two
    different cadence stages.

Prior sends are read from BOTH our own outbox history and the smrtPhone SMS
log, because the manual program is invisible to our database.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from . import config, crm, respond, seed, sender_pool, store
from .knowledge import touches

log = logging.getLogger(__name__)

# Who is in the daily follow-up: everyone currently sitting in the one shared
# outreach preset (config.CAMPAIGN_PRESET, a SiftLine board - "D x D" for this
# account). Unlike Ty's original one-prospector-per-preset design, this account
# assigns leads to multiple callers (Nico, Don, ...) off ONE shared board, and
# each record's own `assigned_to` (resolved via SENDERS_FILE in seed._resolve_sender)
# decides who signs the text and which number pool it sends from. A record with
# no assignee yet, or assigned to someone with no active number pool, is held
# back (never sent unsigned) rather than guessed at - see seed.build().
#
# Where they are in the CALL cadence decides nothing about which text they get;
# that comes from their own text history in next_touch().
STAGE_TOUCHES = [(config.CAMPAIGN_PRESET, 0)]


def _fingerprints() -> dict[int, list[str]]:
    """A distinctive literal snippet from each variant, per touch.

    Merge fields are stripped, so what remains is the fixed wording that
    identifies which touch a historical message came from.
    """
    out: dict[int, list[str]] = {}
    for touch, (pool, noname) in enumerate(touches.POOLS, start=1):
        marks = []
        for template in list(pool) + list(noname):
            # Longest literal run between merge fields, lowercased.
            literals = [p.strip() for p in re.split(r"\{[a-z]+\}", template)]
            best = max(literals, key=len)
            if len(best) >= 18:
                marks.append(re.sub(r"\s+", " ", best.lower())[:60])
        out[touch] = marks
    return out


_FP = _fingerprints()


def identify_touch(body: str) -> Optional[int]:
    """Which touch (1-4) an already-sent message came from, if any."""
    text = re.sub(r"\s+", " ", (body or "").lower())
    if not text:
        return None
    for touch, marks in _FP.items():
        for mark in marks:
            if mark and mark in text:
                return touch
    return None


def prior_touches(sms_log_rows: Optional[list] = None) -> dict[str, dict]:
    """phone -> {"touches": {n}, "last": iso date}, ours AND smrtPhone's log.

    The date matters as much as the set. Progression is per PERSON on a clock,
    so the question at build time is not only "which touches has this number
    had" but "was the last one long enough ago to send the next".
    """
    history: dict[str, dict] = {}

    def note(phone: str, touch: int, when: str) -> None:
        if not phone or not touch:
            return
        entry = history.setdefault(phone, {"touches": set(), "last": ""})
        entry["touches"].add(touch)
        if when and when > entry["last"]:
            entry["last"] = when

    for row in store._conn().execute(
        "SELECT phone, body, created_at FROM messages WHERE direction='out'"
    ):
        note(row["phone"], identify_touch(row["body"]), str(row["created_at"] or ""))

    from . import reconcile

    for row in sms_log_rows or []:
        if (row.get("direction") or "").lower() != "outbound":
            continue
        note(
            store.clean_phone(row.get("toNum")),
            identify_touch(reconcile._clean(row.get("content"))),
            str(row.get("date") or row.get("createdAt") or ""),
        )
    return history


def next_touch(entry: Optional[dict], min_days: int, today: date) -> tuple[Optional[int], str]:
    """Which touch this person is due, and why not if they are not.

    Every owner walks the same four touches in order (Ty, 2026-08-11): the
    identity check, the resend, the soft ask, the goodbye. Traction comes from
    completing that sequence from ONE number, not from a single well-aimed text.

    The first build tied each touch to a CRM call-attempt stage, which quietly
    capped most people at touch 1: a record sitting in Ready to Call never
    advances a stage on its own, so it never earned touch 2 and the campaign
    looked exhausted after a day. Progression is now the person's own history.
    """
    touches_had = (entry or {}).get("touches") or set()
    if not touches_had:
        return 1, ""

    done = max(touches_had)
    if done >= 4:
        return None, "completed all four touches"

    last = (entry or {}).get("last") or ""
    if last:
        try:
            when = datetime.fromisoformat(last.replace("Z", "+00:00")).date()
        except ValueError:
            when = None
        if when and (today - when).days < min_days:
            waited = (today - when).days
            return None, f"touch {done} was {waited}d ago, waiting {min_days}d"
    return done + 1, ""


@dataclass
class Plan:
    candidates: list = field(default_factory=list)
    per_stage: dict = field(default_factory=dict)
    per_touch: dict = field(default_factory=dict)
    skipped_already_touched: int = 0
    skipped_duplicate_person: int = 0
    skipped_waiting: int = 0
    skipped_completed: int = 0
    hit_cap: bool = False


def build(sender_fallback: str = "", log_pages: int = 6,
          min_days: Optional[int] = None, today: Optional[date] = None,
          limit: int = 0) -> Plan:
    """Assemble one run across every cadence stage. Sends nothing.

    Every eligible person is advanced to the NEXT touch they have not had,
    spaced by whole days. A stage is only a source of people now, not the thing
    that decides which text they get.
    """
    from . import reconcile

    min_days = config.TOUCH_GAP_DAYS if min_days is None else min_days
    today = today or datetime.now(timezone.utc).date()

    try:
        sms_rows = reconcile.fetch_log(pages=log_pages)
        log.info("read %s rows of smrtPhone SMS history", len(sms_rows))
    except Exception as exc:  # noqa: BLE001 - fall back to our own history only
        log.warning("could not read the SMS log (%s); using our history only", exc)
        sms_rows = []

    history = prior_touches(sms_rows)
    plan = Plan()
    seen_phone: set = set()

    for title, _stage_touch in STAGE_TOUCHES:
        rows, matched = seed.from_preset(title)
        if not matched:
            plan.per_stage[title] = {"error": "preset not found"}
            continue

        stage_ready = 0
        for row in rows:
            # Every Dial First/Second number on the record, not just the one
            # phone the bulk board search happened to summarize (Ty, 2026-09-15:
            # a record with two qualifying numbers was only ever texted on
            # whichever one `from_preset()`'s row picked). Each number tracks
            # its own touch progression below, exactly like a person with two
            # properties already does one row down.
            phones = _qualifying_phones(row)
            if phones is None:
                continue  # CRM outage reading this record; skip quietly, retried tomorrow

            for phone in phones:
                if phone in seen_phone:
                    plan.skipped_duplicate_person += 1
                    continue

                touch, why = next_touch(history.get(phone), min_days, today)
                if touch is None:
                    if "completed" in why:
                        plan.skipped_completed += 1
                    else:
                        plan.skipped_waiting += 1
                    continue

                phone_row = dict(row, phone=phone)
                built = seed.build([phone_row], touch=touch, sender_fallback=sender_fallback)
                cand = built[0] if built else None
                if not cand or cand.status != "ready":
                    continue

                seen_phone.add(phone)
                cand.touch = touch  # type: ignore[attr-defined]
                plan.per_touch[touch] = plan.per_touch.get(touch, 0) + 1
                plan.candidates.append(cand)
                stage_ready += 1

                # Stop as soon as the day is full. Vetting a candidate costs a
                # CRM read, and the scheduler runs inside the worker pass, so
                # building the whole book every morning would block reply
                # processing for minutes to then discard most of it at the
                # cap. Sources are in priority order, so stopping early keeps
                # the best leads.
                if limit and len(plan.candidates) >= limit:
                    plan.per_stage[title] = {
                        "mobile_records": len(rows), "ready": stage_ready, "stopped_at_cap": True,
                    }
                    plan.hit_cap = True
                    return plan

        plan.per_stage[title] = {"mobile_records": len(rows), "ready": stage_ready}

    # Continue anyone already mid-cadence whose record no longer sits on the
    # "D x D" board at all (Ty, 2026-09-17: touch 1 went out, then the record
    # moved through the normal call-cadence lifecycle - DEALS X DOOR -> New
    # marketing -> Longer Term Follow ups, etc. - and touches 2-4 silently
    # never fired again, because the loop above only ever looks at what is
    # CURRENTLY on the one board). Board membership was never a safety check;
    # every guard that actually matters (dial tier, phone status, suppression,
    # conversation state) is re-verified live against the CRM record inside
    # `seed.build()` regardless of what board it is on. So a phone we already
    # started texting keeps its sequence by reading ITS OWN history and the
    # context recorded in `phone_map` at seed time, not by re-querying a board.
    continuing_ready = 0
    for phone in history:
        if phone in seen_phone:
            continue  # already handled above (still on the board today)

        touch, why = next_touch(history.get(phone), min_days, today)
        if touch is None:
            if "completed" in why:
                plan.skipped_completed += 1
            else:
                plan.skipped_waiting += 1
            continue

        mapped = store.lookup_phone(phone)
        if not mapped or not mapped.get("record_uuid"):
            continue  # no context to build from; nothing lost, just can't continue it here
        ctx = mapped.get("context") or {}
        row = {
            "uuid": mapped["record_uuid"],
            "phone": phone,
            "street": ctx.get("street") or mapped.get("address") or "",
            "city": ctx.get("city") or "",
            "county": ctx.get("county") or "",
            "first": ctx.get("owner_first") or mapped.get("first_name") or "",
            "assigned": ctx.get("assigned_name") or "",
        }

        built = seed.build([row], touch=touch, sender_fallback=sender_fallback)
        cand = built[0] if built else None
        if not cand or cand.status != "ready":
            continue

        seen_phone.add(phone)
        cand.touch = touch  # type: ignore[attr-defined]
        plan.per_touch[touch] = plan.per_touch.get(touch, 0) + 1
        plan.candidates.append(cand)
        continuing_ready += 1

        if limit and len(plan.candidates) >= limit:
            plan.per_stage["_continuing_off_board"] = {
                "ready": continuing_ready, "stopped_at_cap": True,
            }
            plan.hit_cap = True
            return plan

    plan.per_stage["_continuing_off_board"] = {"ready": continuing_ready}
    return plan


def _qualifying_phones(row: dict) -> Optional[list[str]]:
    """Every number on this record worth texting, cheapest check first.

    With a live record uuid this reads the owner's WHOLE phone list in one
    fetch (`crm.record_dial_tier_phones`) rather than trusting the single
    `phone` field the bulk board search summarized the record as. Without a
    record uuid or a live CRM (the CSV/offline path) it falls back to that one
    summary phone, which is exactly what every caller already did before this.

    Returns None only when the record could not be read at all (a CRM outage,
    distinct from "read fine, nothing qualifies", which returns an empty list).
    """
    record_uuid = row.get("uuid") or ""
    if record_uuid and crm.client():
        found, ok = crm.record_dial_tier_phones(record_uuid)
        if not ok:
            return None
        return [phone for phone, _tier in found]
    fallback = store.clean_phone(row.get("phone"))
    return [fallback] if fallback else []


def summary(plan: Plan) -> str:
    lines = [f"{'stage':32} {'mobile':>7} {'to send':>8}"]
    lines.append("-" * 50)
    for title, info in plan.per_stage.items():
        if "error" in info:
            lines.append(f"{title:32} {info['error']}")
            continue
        lines.append(f"{title:32} {info['mobile_records']:>7} {info['ready']:>8}")
    lines.append("-" * 50)
    lines.append(f"{'TOTAL':32} {'':>7} {len(plan.candidates):>8}")
    lines.append("")
    by_touch = ", ".join(f"touch {t}: {n}" for t, n in sorted(plan.per_touch.items()))
    lines.append(f"sending                         : {by_touch or 'nothing'}")
    lines.append(f"waiting out the {config.TOUCH_GAP_DAYS}-day gap        : {plan.skipped_waiting}")
    lines.append(f"finished all four touches       : {plan.skipped_completed}")
    lines.append(f"same person in 2 stages         : {plan.skipped_duplicate_person}")
    cap = sender_pool.capacity_today()
    lines.append(f"pool capacity today             : {cap['remaining']} of {cap['capacity']}")
    return "\n".join(lines)


# Reasons a phone is not one the campaign will ever text, in the order they
# are checked (matches seed.from_preset's own gate order).
_SKIP_STATUSES = {"DNC", "CORRECT_DNC", "WRONG_DNC", "WRONG", "DEAD"}
_GOOD_TIERS = {"Dial First", "Dial Second"}


def board_funnel(preset: Optional[str] = None) -> dict:
    """Why the whole board isn't the eligible pool, in one cheap pass.

    Answers "why did today's run only touch N people" without the per-record
    CRM reads build() needs for assigned_to/conversation state: phone type,
    status, doNotCall and tags are all present on the bulk board search
    response, so this is a single paginated fetch, not N throttled calls.
    Built after a live run where the answer required an ad-hoc script and a
    manual walk through the numbers - this makes it a standing fact instead.
    """
    must, matched = crm.resolve_preset(preset or config.CAMPAIGN_PRESET)
    if not must:
        return {"error": f"preset {preset or config.CAMPAIGN_PRESET!r} not found"}
    rows = crm.fetch_cohort(must, limit=0)

    tier_map = crm.dial_tier_uuids() or {}
    uuid_to_label = {v: k for k, v in tier_map.items()}

    counts = {
        "total": len(rows), "do_not_call": 0, "dead_or_wrong": 0, "no_phone": 0,
        "never_validated": 0, "low_tier_mobile": 0, "non_mobile_tagged": 0, "eligible": 0,
    }
    for r in rows:
        phone = r.get("phone") if isinstance(r.get("phone"), dict) else {}
        if not phone or not phone.get("number"):
            counts["no_phone"] += 1
            continue
        if phone.get("doNotCall"):
            counts["do_not_call"] += 1
            continue
        if (phone.get("status") or "").upper() in _SKIP_STATUSES:
            counts["dead_or_wrong"] += 1
            continue
        tiers = {uuid_to_label.get(t) for t in (phone.get("tags") or []) if t in uuid_to_label}
        if not tiers:
            counts["never_validated"] += 1
            continue
        if (phone.get("type") or "").upper() != "MOBILE":
            counts["non_mobile_tagged"] += 1
            continue
        counts["eligible" if tiers & _GOOD_TIERS else "low_tier_mobile"] += 1
    return counts


def funnel_summary(counts: dict) -> str:
    """One paragraph version - CLI/log use, where a code fence would not render."""
    if "error" in counts:
        return counts["error"]
    return (
        f"{counts['total']} on the board -> {counts['eligible']} eligible today "
        f"(Dial First/Second, mobile)\n"
        f"  {counts['do_not_call']} Do Not Call, {counts['non_mobile_tagged']} scored but landline/VOIP, "
        f"{counts['never_validated']} never phone-validated\n"
        f"  {counts['low_tier_mobile']} mobile but Dial Third/Fourth/Drop, "
        f"{counts['dead_or_wrong']} dead/wrong number, {counts['no_phone']} no phone on record"
    )


def funnel_table(counts: dict) -> str:
    """Slack version: a monospace table in a code fence (mrkdwn has no real tables).

    Two blocks - the headline (total -> eligible) then the reasons the rest
    aren't - so the shape reads as a funnel rather than a flat list of counts.
    """
    if "error" in counts:
        return counts["error"]
    rows = [
        ("Total on board", counts["total"]),
        ("Eligible today (mobile, Dial 1st/2nd)", counts["eligible"]),
        None,
        ("Do Not Call", counts["do_not_call"]),
        ("Scored, but landline/VOIP", counts["non_mobile_tagged"]),
        ("Never phone-validated", counts["never_validated"]),
        ("Mobile, but Dial 3rd/4th/Drop", counts["low_tier_mobile"]),
        ("Dead/wrong number", counts["dead_or_wrong"]),
        ("No phone on record", counts["no_phone"]),
    ]
    width = max(len(label) for row in rows if row for label, _ in [row])
    lines = []
    for row in rows:
        if row is None:
            lines.append("-" * (width + 7))
            continue
        label, value = row
        lines.append(f"{label:<{width}} {value:>5}")
    return "```\n" + "\n".join(lines) + "\n```"
