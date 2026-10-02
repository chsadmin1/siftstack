"""18:00 stage: read today's batch, check each record's current Call
Disposition value, and for anything Wendy marked Left Voicemail today, queue
the matching post-call follow-up text.

Custom-field value shape verified live 2026-08-27 against
GET /api/internal/property/{uuid}/custom-field/:
    [{"custom_field": {"uuid": "<field uuid>", "label": "...", ...},
      "property": "...", "value": "<option uuid for a select field>", ...}]

    python -m sms_agent.daily25.voicemail_run --doctor
    python -m sms_agent.daily25.voicemail_run --dry-run
    python -m sms_agent.daily25.voicemail_run --commit --limit 1   # verify one first
    python -m sms_agent.daily25.voicemail_run --commit
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

if __package__ in (None, ""):  # allow `python src/sms_agent/daily25/voicemail_run.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "sms_agent.daily25"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # src/ (or /app), for siftline_kpi
import siftline_kpi  # noqa: E402

from .. import respond, store  # noqa: E402
from . import batch, config, texting  # noqa: E402

log = logging.getLogger(__name__)

VOICEMAIL_INTENT = "daily25_postcall_voicemail"


def _today() -> str:
    return datetime.datetime.now(ZoneInfo(config.BUSINESS_TIMEZONE)).date().isoformat()


def doctor() -> int:
    print("Daily 35 (voicemail follow-up) wiring check\n" + "=" * 40)
    today_iso = _today()
    rows = batch.load(today_iso)
    print(f"  [{'OK ' if rows else 'MISS'}]  today's batch ({today_iso}): {len(rows)} records"
          + ("" if rows else " -- run select_run first"))
    print(f"  Call Disposition field: {config.CALL_DISPOSITION_FIELD_UUID}")
    print(f"  Left Voicemail option:  {config.DISPOSITION_LEFT_VOICEMAIL_OPTION_UUID}")
    return 0 if rows else 1


def _left_voicemail_today(api, property_uuid: str) -> bool:
    r = api.call(f"/api/internal/property/{property_uuid}/custom-field/")
    rows = r.get("results") or r.get("data") or []
    for row in rows:
        field = row.get("custom_field") or {}
        if field.get("uuid") == config.CALL_DISPOSITION_FIELD_UUID:
            return row.get("value") == config.DISPOSITION_LEFT_VOICEMAIL_OPTION_UUID
    return False


def run(commit: bool, limit: int = 0) -> dict:
    api = siftline_kpi.Api()
    store.init()
    today_iso = _today()
    rows = batch.load(today_iso)
    if limit:
        rows = rows[:limit]

    left_voicemail = queued = skipped = errors = 0
    skip_reasons: dict[str, int] = {}
    cursor = datetime.datetime.now(timezone.utc)
    detail: list[dict] = []

    for i, cand in enumerate(rows):
        row = {"uuid": cand["property_uuid"], "street": cand["street"]}
        try:
            if not _left_voicemail_today(api, cand["property_uuid"]):
                continue
            left_voicemail += 1

            phone = store.clean_phone(cand.get("phone", ""))
            if not phone:
                raise ValueError("no usable phone")

            reason = store.is_suppressed(phone)
            if reason:
                row["skip"] = f"suppressed ({reason})"
                skip_reasons["suppressed"] = skip_reasons.get("suppressed", 0) + 1
                skipped += 1
                detail.append(row)
                continue

            text = texting.render_postcall_left_voicemail(cand)
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

            not_before = cursor.isoformat(timespec="seconds")
            store.queue_message(
                phone, text, from_number=number, not_before=not_before, status="queued",
                intent=VOICEMAIL_INTENT, confidence=1.0,
            )
            row["not_before"] = not_before
            cursor += timedelta(seconds=config.SEND_STAGGER_SECONDS)
            queued += 1
        except Exception as exc:  # noqa: BLE001 - one record must never abort the rest
            errors += 1
            row["error"] = str(exc)[:300]
            log.exception("record %s failed", cand.get("property_uuid"))
        detail.append(row)

    return {
        "date": today_iso, "batch_size": len(rows), "left_voicemail": left_voicemail,
        "queued": queued, "skipped": skipped,
        "skip_reasons": ", ".join(f"{k}={v}" for k, v in skip_reasons.items()) or "none",
        "errors": errors, "detail": detail, "committed": commit,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Daily 35 -- 18:00 voicemail follow-up")
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

        slack.post_voicemail_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
