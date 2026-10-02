"""09:00 stage: read today's batch (persisted by select_run at 08:00), apply
the cooldown/suppression/validator gates, and queue each pre-call text --
strict alternation between Wendy's two numbers, ~2 minutes apart, via the
same outbox every other outbound message goes through (quiet hours and
per-number caps still apply exactly as normal).

    python -m sms_agent.daily25.send_run --doctor
    python -m sms_agent.daily25.send_run --dry-run
    python -m sms_agent.daily25.send_run --commit --limit 1   # verify one first
    python -m sms_agent.daily25.send_run --commit
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from datetime import timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__ in (None, ""):  # allow `python src/sms_agent/daily25/send_run.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "sms_agent.daily25"

from .. import respond, store  # noqa: E402
from . import batch, config, scoring, texting  # noqa: E402

log = logging.getLogger(__name__)


def _today() -> str:
    return datetime.datetime.now(ZoneInfo(config.BUSINESS_TIMEZONE)).date().isoformat()


def doctor() -> int:
    print("Daily 35 (send) wiring check\n" + "=" * 40)
    today_iso = _today()
    rows = batch.load(today_iso)
    print(f"  [{'OK ' if rows else 'MISS'}]  today's batch ({today_iso}): {len(rows)} records"
          + ("" if rows else " -- run select_run first"))
    print(f"  Wendy numbers (alternated): {config.WENDY_NUMBERS}")
    print(f"  stagger: {config.SEND_STAGGER_SECONDS}s")
    return 0 if rows else 1


def run(commit: bool, limit: int = 0) -> dict:
    store.init()
    today_iso = _today()
    rows = batch.load(today_iso)
    if limit:
        rows = rows[:limit]
    recently_texted = scoring.recently_texted_phones()

    queued = skipped = errors = 0
    skip_reasons: dict[str, int] = {}
    cursor = datetime.datetime.now(timezone.utc)
    detail: list[dict] = []

    for i, cand in enumerate(rows):
        row = {"uuid": cand["property_uuid"], "street": cand["street"]}
        try:
            phone = store.clean_phone(cand.get("phone", ""))
            if not phone:
                raise ValueError("no usable phone")

            if scoring.cooldown_blocked(cand, recently_texted):
                row["skip"] = "cooldown"
                skip_reasons["cooldown"] = skip_reasons.get("cooldown", 0) + 1
                skipped += 1
                detail.append(row)
                continue

            reason = store.is_suppressed(phone)
            if reason:
                row["skip"] = f"suppressed ({reason})"
                skip_reasons["suppressed"] = skip_reasons.get("suppressed", 0) + 1
                skipped += 1
                detail.append(row)
                continue

            text = texting.render_precall(cand)
            ok, problems = respond.validate(text, max_questions=1)
            if not ok:
                row["skip"] = "failed content gate: " + "; ".join(problems)
                skip_reasons["content_gate"] = skip_reasons.get("content_gate", 0) + 1
                skipped += 1
                detail.append(row)
                continue

            number = config.WENDY_NUMBERS[i % len(config.WENDY_NUMBERS)]
            row["from_number"] = number
            row["text_preview"] = text[:80]

            if not commit:
                detail.append(row)
                continue

            conv = store.ensure_conversation(phone, from_number=number, record_uuid=cand["property_uuid"])
            if conv.get("from_number") != number:
                row["skip"] = f"sticky to {conv.get('from_number')}, not overriding to {number}"
                skip_reasons["sticky_mismatch"] = skip_reasons.get("sticky_mismatch", 0) + 1
                skipped += 1
                detail.append(row)
                continue
            if conv.get("state") not in ("active", None, ""):
                row["skip"] = f"conversation is {conv.get('state')}"
                skip_reasons["conversation_state"] = skip_reasons.get("conversation_state", 0) + 1
                skipped += 1
                detail.append(row)
                continue

            store.map_phone(
                phone, record_uuid=cand["property_uuid"], first_name=cand.get("owner_first") or "",
                address=cand["street"],
                context={"owner_first": cand.get("owner_first") or "", "street": cand["street"],
                        "city": cand.get("city") or "", "assigned_name": config.WENDY_NAME},
            )
            not_before = cursor.isoformat(timespec="seconds")
            store.queue_message(
                phone, text, from_number=number, not_before=not_before, status="queued",
                intent=config.PRECALL_INTENT, confidence=1.0,
            )
            row["not_before"] = not_before
            cursor += timedelta(seconds=config.SEND_STAGGER_SECONDS)
            queued += 1
        except Exception as exc:  # noqa: BLE001 - one record must never abort the rest
            errors += 1
            row["error"] = str(exc)[:300]
            log.exception("record %s failed", cand.get("property_uuid"))
        detail.append(row)

    span = f"{queued} records over ~{queued * config.SEND_STAGGER_SECONDS // 60} min" if queued else ""

    return {
        "date": today_iso, "batch_size": len(rows), "queued": queued, "skipped": skipped,
        "skip_reasons": ", ".join(f"{k}={v}" for k, v in skip_reasons.items()) or "none",
        "errors": errors, "span": span, "detail": detail, "committed": commit,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Daily 35 -- 09:00 send")
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.doctor:
        return doctor()

    commit = bool(args.commit and not args.dry_run)
    summary = run(commit=commit, limit=args.limit)
    print(json.dumps({k: v for k, v in summary.items() if k != "detail"}, indent=2))
    print("\nrecords:")
    for row in summary["detail"]:
        print(" ", json.dumps(row))

    if commit:
        from . import slack

        slack.post_send_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
