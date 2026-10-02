"""The "DxD Exhausted" farewell cadence: Phil's checklist, built.

Records that sat through the whole D x D calling/texting run without a deal
land in a SiftLine column named "DxD Exhausted", on the "New marketing"
board. Verified live 2026-08-27: the filter preset "DxD Exhausted" IS that
exact board+column (its `filters.must` is `{"any_boards": [<New marketing
uuid>], "any_columns": [<DxD Exhausted uuid>]}`), so pulling through the same
`sms_agent.crm.resolve_preset` / `fetch_cohort` machinery every other cadence
in this codebase already uses inherits every suppression rule already
encoded there, with no second list to keep in sync.

The schedule (Phil's checklist, unchanged):

    day  7  email
    day  9  email + text
    day 11  text
    day 13  text + email
    day 15  text   <- final farewell: "sorry we couldn't make a deal",
                      "wish I could have gotten a hold of you"

Two design calls made building this, both worth stating up front:

1. **The anchor is OUR OWN first-seen date for the record, not the SiftLine
   card's column-entry timestamp.** CLAUDE.md already documents why:
   `.../card/{uuid}/timeline/` is a periodically-recomputed counter, not a
   live clock, and came back null for the great majority of freshly-moved
   cards on a live 235-card sample. Anchoring on it would silently stall the
   whole cadence for any record whose timeline entry hasn't recomputed yet.
   `campaign.py`'s next_touch() already made the same call for the main D x D
   drip (a person's own send history over CRM state), and this follows it.

2. **A day is marked "fired" the moment we commit to attempting it** (text:
   once it is written to the outbox as `held`; email: once the send call
   returns, success or failure), not once a human has released a held text
   or a bounced email is confirmed delivered. The alternative - waiting for
   confirmed delivery - would mean an un-released held text re-queues a
   duplicate candidate for the same day on every future run. `release()` is
   still the deliberate go/no-go for texts leaving the building; this only
   governs whether the SCHEDULER thinks that day is spoken for.

Every guard the two channels already enforce (suppression, dial tier,
opt-out tags, live-conversation checks, name hygiene, sender resolution, the
human-voice / CAN-SPAM content validators) is reused as-is via
`sms_agent.seed.build(..., render_fn=...)` and
`email_agent.seed.build(..., render_fn=..., gate=False)` - this module only
supplies the copy and the day-based schedule, not a second vetting pipeline.

    python src/exhausted_cadence.py doctor
    python src/exhausted_cadence.py plan -v
    python src/exhausted_cadence.py run --commit
    python src/exhausted_cadence.py release
    python src/exhausted_cadence.py status
    python src/exhausted_cadence.py selftest
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sms_agent import config as sms_config  # noqa: E402
from sms_agent import crm as sms_crm  # noqa: E402
from sms_agent import seed as sms_seed  # noqa: E402
from sms_agent import store as sms_store  # noqa: E402
from sms_agent.knowledge import touches as sms_touches  # noqa: E402
from email_agent import config as email_config  # noqa: E402
from email_agent import mailer  # noqa: E402
from email_agent import seed as email_seed  # noqa: E402
from email_agent import store as email_store  # noqa: E402
from email_agent.knowledge import touches as email_touches  # noqa: E402

PRESET = (os.environ.get("EXHAUSTED_CADENCE_PRESET", "") or "DxD Exhausted").strip()
STATE_FILE = Path(
    os.environ.get("EXHAUSTED_CADENCE_STATE_FILE", "")
    or str(ROOT / "output" / "exhausted_cadence_state.json")
)

# day -> which channel(s) fire that day. Phil's checklist, verbatim.
SCHEDULE: dict[int, tuple[str, ...]] = {
    7: ("email",),
    9: ("email", "text"),
    11: ("text",),
    13: ("text", "email"),
    15: ("text",),
}


# --------------------------------------------------------------------- state

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        log.warning("could not read %s (%s); starting fresh", STATE_FILE, exc)
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_FILE)


def next_due_day(fired: set, elapsed: int) -> Optional[int]:
    """The earliest schedule day this record has reached but not had.

    One day per run, even when several are overdue at once: a job that has
    not run in a while should catch up gradually, not burst every missed
    touch on the day it comes back.
    """
    due = sorted(d for d in SCHEDULE if d <= elapsed and d not in fired)
    return due[0] if due else None


# ------------------------------------------------------------------- cohort

def pull_cohort(limit: int = 0) -> tuple[list, str]:
    """Full CRM records currently in the preset. Needs the full record (not
    the slim search row) because both a phone AND an owner email are read off
    it, same reasoning `email_agent.seed.from_preset` already documents."""
    must, matched = sms_crm.resolve_preset(PRESET)
    if not must:
        return [], ""
    records = []
    for slim in sms_crm.fetch_cohort(must, limit=limit):
        uuid = slim.get("uuid")
        if not uuid:
            continue
        rec = sms_crm.get_record(uuid)
        if rec:
            records.append(rec)
    return records, matched


def _best_mobile_phone(rec: dict) -> str:
    """First Dial First/Second mobile number on the record, or "".

    Same allow-list `sms_agent.seed.build` enforces on the main drip (Ty,
    2026-08-11): texting a Dial Third/Fourth/Drop or an untagged number spends
    a segment on a line already judged unlikely to reach the owner.
    """
    owner = rec.get("owner") if isinstance(rec.get("owner"), dict) else {}
    tiers = {"Dial First", "Dial Second"}
    for p in owner.get("phones") or []:
        if not isinstance(p, dict):
            continue
        if (p.get("status") or "").upper() in sms_seed.SKIP_PHONE_STATUSES:
            continue
        if (p.get("type") or "").upper() != "MOBILE":
            continue
        names = {
            (t.get("title") or t.get("name") or t.get("tag")) if isinstance(t, dict) else str(t)
            for t in (p.get("tags") or [])
        }
        if names & tiers:
            digits = sms_store.clean_phone(p.get("number"))
            if len(digits) == 10:
                return digits
    return ""


def _sms_row(rec: dict) -> dict:
    """Full record -> the row shape `sms_agent.seed.build` expects."""
    addr = rec.get("address") or {}
    owner = rec.get("owner") or {}
    assigned = rec.get("assigned_to")
    if isinstance(assigned, dict):
        assigned = assigned.get("uuid") or assigned.get("id") or ""
    return {
        "phone": _best_mobile_phone(rec),
        "uuid": rec.get("uuid") or "",
        "street": addr.get("street") or "",
        "city": addr.get("city") or "",
        "county": addr.get("county") or "",
        "first": owner.get("first_name") or "",
        "last": owner.get("last_name") or "",
        "owner": owner.get("company")
        or " ".join(x for x in (owner.get("first_name"), owner.get("last_name")) if x),
        "assigned": assigned or "",
    }


# --------------------------------------------------------------------- plan

@dataclass
class Plan:
    cohort_size: int = 0
    new_entrants: int = 0
    not_yet_due: int = 0
    per_day: dict = field(default_factory=dict)
    text_candidates: list = field(default_factory=list)
    email_candidates: list = field(default_factory=list)
    held_reasons: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)  # mutated copy; caller decides whether to persist


def _note_held(plan: Plan, cand) -> None:
    for reason in cand.reasons:
        key = reason.split(":")[0].split("(")[0].strip()
        plan.held_reasons[key] = plan.held_reasons.get(key, 0) + 1


def build_plan(sender_fallback: str = "", today: Optional[date] = None, limit: int = 0) -> Plan:
    """Who is due today, on which channel(s), rendered and vetted. Sends
    nothing, queues nothing, writes nothing - `state` on the returned Plan is
    a mutated in-memory copy; `run()` decides whether to persist it."""
    today = today or datetime.now(timezone.utc).date()
    state = load_state()
    records, matched = pull_cohort(limit=limit)
    plan = Plan(cohort_size=len(records), state=state)
    if not matched:
        log.warning("preset %r not found; nothing to plan", PRESET)
        return plan

    for rec in records:
        uuid = rec.get("uuid") or ""
        if not uuid:
            continue
        entry = state.get(uuid)
        if entry is None:
            # Day 0: the first time we have ever observed this record in the
            # cohort. Nothing is due yet - the earliest schedule day is 7.
            state[uuid] = {"first_seen": today.isoformat(), "fired": []}
            plan.new_entrants += 1
            continue

        first_seen = date.fromisoformat(entry["first_seen"])
        elapsed = (today - first_seen).days
        fired = set(entry.get("fired") or [])
        day = next_due_day(fired, elapsed)
        if day is None:
            plan.not_yet_due += 1
            continue

        channels = SCHEDULE[day]

        if "text" in channels:
            row = _sms_row(rec)
            built = sms_seed.build(
                [row], touch=day, sender_fallback=sender_fallback,
                render_fn=lambda **kw: sms_touches.render_farewell(day, **kw),
            )
            cand = built[0] if built else None
            if cand and cand.status == "ready":
                # sms_agent.seed.build() does NOT set Candidate.touch itself
                # (campaign.py, its only other caller, sets it by hand after
                # the call for the same reason) - without this, every text
                # candidate defaults to touch=0 and run() groups them all
                # into one wrong "day 0" queue intent. Caught by a synthetic
                # smoke test before this ever ran live.
                cand.touch = day
                plan.text_candidates.append(cand)
            elif cand:
                _note_held(plan, cand)

        if "email" in channels:
            built = email_seed.build(
                [rec], touch=day, sender_fallback=sender_fallback, gate=False,
                render_fn=lambda **kw: email_touches.render_farewell(day, **kw),
            )
            cand = built[0] if built else None
            if cand and cand.status == "ready":
                plan.email_candidates.append(cand)
            elif cand:
                _note_held(plan, cand)

        plan.per_day[day] = plan.per_day.get(day, 0) + 1
        # Fired on ATTEMPT, not on confirmed delivery - see module docstring.
        # A record with nothing usable on either channel today will simply
        # fail the same way again at the next scheduled day, logged each time
        # rather than retried forever on today's day.
        entry["fired"] = sorted(fired | {day})

    return plan


# ------------------------------------------------------------------- queue

def queue_texts(candidates: list, day: int) -> dict:
    return sms_seed.queue(candidates, touch=day, intent=f"exhausted_day_{day}")


def release_texts(day: Optional[int] = None, limit: int = 0) -> int:
    """The deliberate go/no-go for the text side. `day=None` releases every
    scheduled text day at once."""
    days = [day] if day is not None else [d for d, ch in SCHEDULE.items() if "text" in ch]
    return sum(sms_seed.release_intent(f"exhausted_day_{d}", limit) for d in days)


def send_emails(candidates: list, commit: bool = False) -> dict:
    """Mirrors `email_agent.cli.cmd_send`: synchronous, gated on `commit` AND
    `email_config.DRY_RUN` independently. NOTE: as of 2026-08-27 the connected
    mailbox's send-email/ call 500s on this account for every payload tried
    (see email_agent/README.md, Known Gaps) - this will fail the same way
    until DataSift resolves that, which is a platform issue, not this code."""
    if not candidates:
        return {"sent": 0, "failed": 0}
    if not commit or email_config.DRY_RUN:
        return {"preview": len(candidates)}
    mailbox = mailer.resolve_mailbox()
    if not mailbox:
        return {"error": "no connected mailbox resolved; run email_agent/cli.py doctor first"}
    integration_id = str(mailbox.get("id") or mailbox.get("uuid"))
    sent, failed = 0, 0
    for c in candidates:
        try:
            mailer.send_email(integration_id, c.email, c.subject, c.body)
            email_store.log_send(c.email, c.record_uuid, c.touch, c.subject, status="sent")
            sent += 1
        except mailer.MailerError as exc:
            email_store.log_send(c.email, c.record_uuid, c.touch, c.subject, status="failed", error=str(exc))
            failed += 1
        time.sleep(email_config.SEND_SPACING_SECONDS)
    return {"sent": sent, "failed": failed}


def run(sender_fallback: str = "", commit: bool = False, limit: int = 0) -> dict:
    """One day's full pass. Without --commit this is a pure read: nothing is
    queued, nothing is sent, and the schedule state is not persisted, so it
    is always safe to run for a look."""
    plan = build_plan(sender_fallback=sender_fallback, limit=limit)
    result = {
        "cohort": plan.cohort_size,
        "new_entrants": plan.new_entrants,
        "not_yet_due": plan.not_yet_due,
        "per_day": plan.per_day,
        "held_reasons": plan.held_reasons,
    }
    if not commit:
        result["preview"] = True
        result["texts_would_queue"] = len(plan.text_candidates)
        result["emails_would_send"] = len(plan.email_candidates)
        return result

    queued = 0
    for day in sorted({c.touch for c in plan.text_candidates}):
        batch = [c for c in plan.text_candidates if c.touch == day]
        queued += queue_texts(batch, day)["queued"]
    result["texts_queued_held"] = queued
    result["email_result"] = send_emails(plan.email_candidates, commit=commit)

    save_state(plan.state)
    return result


# ------------------------------------------------------------------- status

def status() -> dict:
    state = load_state()
    per_day_completed = {d: 0 for d in SCHEDULE}
    finished = 0
    max_day = max(SCHEDULE)
    for entry in state.values():
        fired = set(entry.get("fired") or [])
        for d in fired:
            if d in per_day_completed:
                per_day_completed[d] += 1
        if max_day in fired:
            finished += 1
    return {
        "tracked": len(state),
        "finished_all_days": finished,
        "per_day_completed": per_day_completed,
        "state_file": str(STATE_FILE),
    }


# ----------------------------------------------------------------------- CLI

def cmd_doctor(args) -> int:
    must, matched = sms_crm.resolve_preset(PRESET)
    print(f"preset           : {PRESET!r} -> {'FOUND as ' + repr(matched) if matched else 'NOT FOUND'}")
    if must:
        print(f"  filter         : {must}")
    print(f"state file       : {STATE_FILE} ({'exists' if STATE_FILE.exists() else 'not created yet'})")
    print(f"schedule         : " + ", ".join(f"day {d}: {'+'.join(ch)}" for d, ch in sorted(SCHEDULE.items())))
    print()
    gaps = email_config.missing()
    if gaps:
        print("email side config gaps (blocks the email leg only, texts are unaffected):")
        for g in gaps:
            print(f"  - {g}")
    else:
        print("email side config: ok")
    print(f"email DRY_RUN    : {email_config.DRY_RUN}")
    print(f"sms DRY_RUN      : {sms_config.DRY_RUN}")
    if not matched:
        return 1
    st = status()
    print(f"\ntracked records  : {st['tracked']}, finished all days: {st['finished_all_days']}")
    return 0


def cmd_plan(args) -> int:
    plan = build_plan(sender_fallback=sms_config.SENDER_NAME, limit=args.limit)
    print(f"cohort: {plan.cohort_size}  new entrants (day 0, nothing due yet): {plan.new_entrants}  "
          f"not yet due: {plan.not_yet_due}")
    if plan.per_day:
        print("due today: " + ", ".join(f"day {d}: {n}" for d, n in sorted(plan.per_day.items())))
    print(f"ready to queue: {len(plan.text_candidates)} text(s), {len(plan.email_candidates)} email(s)")
    if plan.held_reasons:
        print("held reasons: " + ", ".join(f"{k} ({n})" for k, n in
              sorted(plan.held_reasons.items(), key=lambda kv: -kv[1])))
    if args.verbose:
        for c in plan.text_candidates:
            print(f"  TEXT  day {c.touch}  {c.phone:12}  {c.street}  ({c.sender})")
            print(f"    {c.message}")
        for c in plan.email_candidates:
            print(f"  EMAIL day {c.touch}  {c.email:32}  {c.street}  ({c.sender})")
            print(f"    subject: {c.subject}")
    return 0


def cmd_run(args) -> int:
    result = run(sender_fallback=sms_config.SENDER_NAME, commit=args.commit, limit=args.limit)
    print(json.dumps(result, indent=2))
    if not args.commit:
        print("\n(preview only; pass --commit to queue texts (held) and attempt email sends)")
    else:
        print("\ntexts are HELD, not sent - run `release` (the deliberate go/no-go) to let them go out,"
              " same as every other cadence in this codebase.")
    return 0


def cmd_release(args) -> int:
    n = release_texts(day=args.day, limit=args.limit or 0)
    print(f"released {n} held text(s)"
          + (f" for day {args.day}" if args.day else " across every scheduled text day"))
    return 0


def cmd_status(args) -> int:
    print(json.dumps(status(), indent=2))
    return 0


def cmd_selftest(args) -> int:
    import tempfile

    fails = []

    def check(name, cond):
        if cond:
            print(f"ok    {name}")
        else:
            print(f"FAIL  {name}")
            fails.append(name)

    check("schedule matches Phil's checklist",
          SCHEDULE == {7: ("email",), 9: ("email", "text"), 11: ("text",),
                       13: ("text", "email"), 15: ("text",)})

    check("day 0-6 has nothing due", next_due_day(set(), 6) is None)
    check("day 7 becomes due once 7 days have elapsed", next_due_day(set(), 7) == 7)
    check("day 7 already fired -> next due is 9", next_due_day({7}, 10) == 9)
    check("catches up one day at a time, not a burst",
          next_due_day(set(), 20) == 7)
    check("every scheduled day fired means nothing left",
          next_due_day({7, 9, 11, 13, 15}, 999) is None)

    # Every farewell copy variant, both channels, both name/no-name branches,
    # must survive the same validator a live run would run it through. This
    # is the cheapest possible regression guard against a future validator
    # tightening silently breaking hand-written copy nobody re-checks.
    from sms_agent import respond
    for day, (pool, noname) in sms_touches.FAREWELL_POOLS.items():
        for templates, first in ((pool, "Jane"), (noname, "")):
            for i in range(len(templates)):
                msg = sms_touches.render_farewell(
                    day, seed=f"variant{i}", first=first, addr="123 Elm St",
                    city="Knoxville", sender="Adriana",
                )
                ok, problems = respond.validate(msg, max_questions=2)
                label = f"sms farewell day {day} variant {i} ({'named' if first else 'noname'}) validates"
                if not ok:
                    label += ": " + "; ".join(problems)
                check(label, ok)

    from email_agent import validate as email_validate
    for day, (subj_pool, body_pool, subj_noname, body_noname) in email_touches.FAREWELL_POOLS.items():
        for subjects, bodies, first in ((subj_pool, body_pool, "Jane"), (subj_noname, body_noname, "")):
            for i in range(len(bodies)):
                subject, body = email_touches.render_farewell(
                    day, seed=f"variant{i}", first=first, street="123 Elm St",
                    city="Knoxville", sender="Adriana",
                )
                ok, problems = email_validate.validate(body, max_questions=1)
                check(f"email farewell day {day} variant {i} ({'named' if first else 'noname'}) validates", ok)

    check("day 15 text actually uses Phil's closing language",
          any("sorry we couldn't" in t.lower() and "hold of you" in t.lower()
              for t in sms_touches.FAREWELL15 + sms_touches.FAREWELL15_NONAME))
    check("day 13 email actually uses Phil's closing language",
          any("hold of" in b.lower() for b in email_touches.BODY_F13 + email_touches.BODY_F13_NONAME))

    global STATE_FILE
    real_state_file = STATE_FILE
    tmp = Path(tempfile.mkdtemp()) / "state.json"
    STATE_FILE = tmp
    try:
        check("state file starts empty", load_state() == {})
        save_state({"uuid-1": {"first_seen": "2026-08-01", "fired": [7]}})
        check("state roundtrips", load_state()["uuid-1"]["fired"] == [7])
    finally:
        STATE_FILE = real_state_file

    # Full walk of the schedule against a synthetic record, on throwaway
    # databases for both channels. This exists because a live run of it
    # caught a real bug (sms_agent.seed.build() never sets Candidate.touch
    # itself - only campaign.py did, by hand, after the call - so every text
    # candidate here silently defaulted to touch=0 and every farewell text
    # would have queued under the WRONG intent). Zero network: resolve_preset
    # / fetch_cohort / get_record are stubbed, and the dial-tier and sender
    # lookups this path exercises read off the full record / config/
    # sms_senders.json rather than calling out.
    import importlib
    import os as _os

    sms_tmp = tempfile.mkdtemp()
    email_tmp = tempfile.mkdtemp()
    _os.environ["SMS_AGENT_DATA_DIR"] = sms_tmp
    _os.environ["EMAIL_AGENT_DATA_DIR"] = email_tmp
    importlib.reload(sms_config)
    importlib.reload(sms_store)
    importlib.reload(email_config)
    importlib.reload(email_store)

    real_resolve, real_fetch, real_get = (
        sms_crm.resolve_preset, sms_crm.fetch_cohort, sms_crm.get_record,
    )
    fake_uuid = "selftest-fake-record"
    fake_rec = {
        "uuid": fake_uuid,
        "address": {"street": "123 Elm St", "city": "Knoxville", "county": "Knox"},
        "owner": {
            "first_name": "Jane", "last_name": "Doe",
            "phones": [{"number": "8655559999", "type": "MOBILE", "status": "CORRECT",
                        "tags": [{"title": "Dial First"}]}],
            "emails": [{"email": "jane@example.com", "status": "VALID"}],
        },
        "assigned_to": next(iter(sms_config.senders()), ""),
        "tags": [],
    }
    sms_crm.resolve_preset = lambda title: ({"any_boards": ["x"], "any_columns": ["y"]}, title)
    sms_crm.fetch_cohort = lambda must, limit=0: [{"uuid": fake_uuid}]
    sms_crm.get_record = lambda uuid: fake_rec if uuid == fake_uuid else None

    STATE_FILE = Path(tempfile.mkdtemp()) / "state.json"
    try:
        plan0 = build_plan(today=date(2026, 1, 1))
        check("day 0: new entrant, nothing due yet",
              plan0.new_entrants == 1 and not plan0.text_candidates and not plan0.email_candidates)
        save_state(plan0.state)

        expected = {
            7: {"text": 0, "email": 1},
            9: {"text": 1, "email": 1},
            11: {"text": 1, "email": 0},
            13: {"text": 1, "email": 1},
            15: {"text": 1, "email": 0},
        }
        elapsed_dates = {7: date(2026, 1, 8), 9: date(2026, 1, 10), 11: date(2026, 1, 12),
                          13: date(2026, 1, 14), 15: date(2026, 1, 16)}
        for day, when in elapsed_dates.items():
            p = build_plan(today=when)
            exp = expected[day]
            check(f"day {day}: {exp['text']} text, {exp['email']} email candidate(s)",
                  len(p.text_candidates) == exp["text"] and len(p.email_candidates) == exp["email"])
            for c in p.text_candidates:
                check(f"day {day} text candidate carries touch={day} (the bug this guards against)",
                      c.touch == day)
                q = queue_texts([c], c.touch)
                check(f"day {day} text queues under intent exhausted_day_{day}", q["queued"] == 1)
            for c in p.email_candidates:
                check(f"day {day} email candidate carries touch={day}", c.touch == day)
            save_state(p.state)

        row = sms_store._conn().execute(
            "SELECT intent FROM outbox WHERE phone='8655559999' ORDER BY id"
        ).fetchall()
        intents = sorted(r["intent"] for r in row)
        check("every queued text carries its own day's intent, no collisions",
              intents == ["exhausted_day_11", "exhausted_day_13", "exhausted_day_15", "exhausted_day_9"])

        released = release_texts()
        check("release_texts() with no day releases every held text", released == 4)

        final = load_state()
        check("state ends with every scheduled day fired",
              final[fake_uuid]["fired"] == [7, 9, 11, 13, 15])
    finally:
        sms_crm.resolve_preset, sms_crm.fetch_cohort, sms_crm.get_record = (
            real_resolve, real_fetch, real_get,
        )
        STATE_FILE = real_state_file

    print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
    return 0 if not fails else 1


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="DxD Exhausted farewell cadence")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("plan")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--verbose", "-v", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("run")
    p.add_argument("--commit", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("release")
    p.add_argument("--day", type=int, default=None)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("selftest")
    p.set_defaults(func=cmd_selftest)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
