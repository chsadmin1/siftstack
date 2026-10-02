"""Email cadence CLI.

    python src/email_agent/cli.py doctor                     # config + mailbox check
    python src/email_agent/cli.py doctor --test-send          # + one real test email
    python src/email_agent/cli.py plan                        # preview today's batch
    python src/email_agent/cli.py send                        # preview (no --commit = no send)
    python src/email_agent/cli.py send --commit                # the real thing
    python src/email_agent/cli.py send --commit --touch 1      # only touch 1 today
    python src/email_agent/cli.py optout-scan --commit         # suppress mailbox opt-outs, both channels
    python src/email_agent/cli.py selftest                     # zero-network assertions
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from email_agent import config, mailer, optout_scan, seed, store, validate  # noqa: E402
from sms_agent import config as sms_config  # noqa: E402

log = logging.getLogger(__name__)


def cmd_doctor(args) -> int:
    gaps = config.missing()
    print(f"campaign preset  : {config.CAMPAIGN_PRESET}")
    print(f"touch gap days   : {config.TOUCH_GAP_DAYS}")
    print(f"daily cap        : {config.DAILY_CAP}")
    print(f"dry run          : {config.DRY_RUN}")
    print(f"senders file     : {config.SENDERS_FILE} ({'exists' if config.SENDERS_FILE.exists() else 'MISSING'})")
    print()
    if gaps:
        print("config gaps:")
        for g in gaps:
            print(f"  - {g}")
        print()

    result = mailer.doctor()
    if result.get("error"):
        print(f"email-integration: ERROR - {result['error']}")
        return 1
    print(f"JWT mint         : {'ok' if result['jwt'] else 'FAILED'}")
    if not result["integrations"]:
        print("connected mailboxes: NONE - connect one in DataSift Settings > Integrations first")
        return 1
    print(f"connected mailboxes: {len(result['integrations'])}")
    for row in result["integrations"]:
        print(f"  - {row['address']}  (id {row['id']})")
    if result["chosen"]:
        print(f"sending through  : {result['chosen']['address']}  (id {result['chosen']['id']})")
    else:
        print("sending through  : NONE MATCHED - set EMAIL_AGENT_MAILBOX or EMAIL_AGENT_MAILBOX_ID")
        return 1

    if args.test_send:
        if not config.TEST_RECIPIENT:
            print("\n--test-send needs EMAIL_AGENT_PHYSICAL_ADDRESS... set EMAIL_AGENT_TEST_RECIPIENT too")
            return 1
        subject = "email agent test send"
        body = (
            "This is a real test send from the email cadence's doctor command, "
            "confirming the send-email payload shape against your connected mailbox."
        )
        try:
            resp = mailer.send_email(
                str(result["chosen"]["id"]), config.TEST_RECIPIENT, subject, body,
            )
            print(f"\ntest send: OK - {resp}")
        except mailer.MailerError as exc:
            print(f"\ntest send: FAILED - {exc}")
            print("this is the live error body; the send-email field names may need adjusting in mailer.py")
            return 1
    return 0


def _build_plan(args):
    return seed.plan(
        preset=args.preset or config.CAMPAIGN_PRESET,
        limit=args.limit,
        sender_fallback=sms_config.SENDER_NAME,
    )


def cmd_plan(args) -> int:
    candidates = _build_plan(args)
    if not candidates:
        print("nothing ready to send (empty preset, or everyone already mid-cycle/waiting)")
        return 0
    by_touch: dict = {}
    for c in candidates:
        by_touch[c.touch] = by_touch.get(c.touch, 0) + 1
    print(f"{len(candidates)} ready across {len(by_touch)} touch(es): "
          + ", ".join(f"touch {t}: {n}" for t, n in sorted(by_touch.items())))
    if args.verbose:
        for c in candidates:
            print(f"  touch {c.touch}  {c.email:32}  {c.street}  ({c.sender})")
            print(f"    subject: {c.subject}")
    return 0


def cmd_send(args) -> int:
    candidates = _build_plan(args)
    if args.touch:
        candidates = [c for c in candidates if c.touch == args.touch]

    already_today = store.sent_today()
    room = max(0, config.DAILY_CAP - already_today)
    if len(candidates) > room:
        print(f"holding {len(candidates) - room} back: daily cap {config.DAILY_CAP}, "
              f"{already_today} already sent today")
        candidates = candidates[:room]

    if not candidates:
        print("nothing to send")
        return 0

    if not args.commit or config.DRY_RUN:
        print(f"PREVIEW ({len(candidates)} would send; pass --commit and unset EMAIL_AGENT_DRY_RUN to send for real)")
        for c in candidates:
            print(f"  touch {c.touch}  {c.email:32}  {c.street}")
        return 0

    mailbox = mailer.resolve_mailbox()
    if not mailbox:
        print("no connected mailbox resolved; run doctor first")
        return 1
    integration_id = str(mailbox.get("id") or mailbox.get("uuid"))

    sent, failed = 0, 0
    for c in candidates:
        try:
            mailer.send_email(integration_id, c.email, c.subject, c.body)
            store.log_send(c.email, c.record_uuid, c.touch, c.subject, status="sent")
            sent += 1
            print(f"sent  touch {c.touch}  {c.email}")
        except mailer.MailerError as exc:
            store.log_send(c.email, c.record_uuid, c.touch, c.subject, status="failed", error=str(exc))
            failed += 1
            print(f"FAILED  {c.email}  {exc}")
        time.sleep(config.SEND_SPACING_SECONDS)

    print(f"\n{sent} sent, {failed} failed")
    return 0 if failed == 0 else 1


def cmd_optout_scan(args) -> int:
    result = optout_scan.scan(limit=args.limit, commit=args.commit)
    if "error" in result:
        print(result["error"])
        return 1
    print(f"checked {result['checked']} messages, {len(result['opt_outs'])} opt-out(s) found")
    for addr in result["opt_outs"]:
        print(f"  {addr}")
    if not args.commit and result["opt_outs"]:
        print("\n(dry run: pass --commit to suppress locally and tag the record(s) Do Not Market)")
    return 0


def cmd_selftest(args) -> int:
    import os
    import tempfile

    fails = []

    def check(name, cond):
        if cond:
            print(f"ok    {name}")
        else:
            print(f"FAIL  {name}")
            fails.append(name)

    ok, problems = validate.validate("Hi Jane, would you ever consider selling 123 Elm St?")
    check("clean copy validates", ok)

    ok, problems = validate.validate("We can offer $90k for your foreclosure property!!")
    check("money + list-word + shouting copy is rejected", not ok and len(problems) >= 2)

    from email_agent.knowledge import touches
    subj, body = touches.render(1, seed="123 Elm St|Jane Doe", first="Jane", street="123 Elm St",
                                 city="Knoxville", sender="Adriana")
    check("touch 1 renders with the sender's name", "Adriana" in body)
    subj2, body2 = touches.render(1, seed="123 Elm St|Jane Doe", first="Jane", street="123 Elm St",
                                   city="Knoxville", sender="Adriana")
    check("rendering is deterministic for the same record", (subj, body) == (subj2, body2))

    check("clean_first drops initials-only names", touches.clean_first("E A Henry") == "")
    check("clean_first finds a real first name before the surname", touches.clean_first("C Eugene Suthard") == "Eugene")
    check("is_entity flags an LLC", touches.is_entity("158 Old State LLC"))

    tmp = tempfile.mkdtemp()
    os.environ["EMAIL_AGENT_DATA_DIR"] = tmp
    import importlib
    from email_agent import config as cfg
    importlib.reload(cfg)
    from email_agent import store as st
    importlib.reload(st)
    st.suppress("test@example.com", "opt_out")
    check("suppression roundtrips", st.is_suppressed("test@example.com") == "opt_out")
    check("clean_email rejects garbage", st.clean_email("not-an-email") == "")
    st.log_send("jane@example.com", "uuid-1", 1, "subject")
    hist = st.prior_touches("jane@example.com")
    check("a logged send shows up in prior_touches", hist["touches"] == {1})

    from email_agent import seed as sd
    due, why = sd._next_touch({"touches": set()}, min_days=4, today=__import__("datetime").date(2026, 1, 10))
    check("no history means touch 1 is due", due == 1)
    due, why = sd._next_touch({"touches": {1}, "last": "2026-01-08"}, min_days=4,
                               today=__import__("datetime").date(2026, 1, 9))
    check("waiting out the gap holds the next touch", due is None and "waiting" in why)
    due, why = sd._next_touch({"touches": {3}}, min_days=4, today=__import__("datetime").date(2026, 1, 10))
    check("touch 3 is the end of the sequence", due is None and "completed" in why)

    print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
    return 0 if not fails else 1


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Email cadence CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor")
    p.add_argument("--test-send", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("plan")
    p.add_argument("--preset", default="")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--verbose", "-v", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("send")
    p.add_argument("--preset", default="")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--touch", type=int, default=0)
    p.add_argument("--commit", action="store_true")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("optout-scan")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--commit", action="store_true")
    p.set_defaults(func=cmd_optout_scan)

    p = sub.add_parser("selftest")
    p.set_defaults(func=cmd_selftest)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
