"""08:00 stage: pull both boards, score, pick the top 35, create Wendy's call
tasks, Trestle-score any untagged picked phone, draft + write the pre-call
text, persist today's batch for the 09:00/18:00 stages, tag for idempotency.

    python -m sms_agent.daily25.select_run --doctor
    python -m sms_agent.daily25.select_run --dry-run
    python -m sms_agent.daily25.select_run --commit --limit 1   # verify one first
    python -m sms_agent.daily25.select_run --commit
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__ in (None, ""):  # allow `python src/sms_agent/daily25/select_run.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "sms_agent.daily25"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # src/ (or /app), for siftline_kpi
import siftline_kpi  # noqa: E402

from .. import store  # noqa: E402
from . import batch, board, config, phones, scoring, tagging, tasks, texting  # noqa: E402

log = logging.getLogger(__name__)


def _today() -> str:
    return datetime.datetime.now(ZoneInfo(config.BUSINESS_TIMEZONE)).date().isoformat()


def doctor() -> int:
    print("Daily 35 (select) wiring check\n" + "=" * 40)
    ok = True
    try:
        api = siftline_kpi.Api()
        print("  [OK ]  DATASIFT_EMAIL/PASSWORD mint a JWT")
    except SystemExit as exc:
        print(f"  [MISS]  {exc}")
        return 2

    found_uuid = tasks.scan_boards_for_assignee(api, config.BOARDS, config.WENDY_EMAIL)
    match = found_uuid == config.WENDY_UUID
    print(f"  [{'OK ' if match else 'MISS'}]  Wendy's uuid re-confirmed by board scan: "
          f"{found_uuid or '(not found)'} "
          f"{'matches config' if match else 'DOES NOT MATCH config.WENDY_UUID ' + config.WENDY_UUID}")
    ok = ok and match

    idx = texting.field_index(api)
    present = config.PRECALL_FIELD_LABEL in idx
    print(f"  [{'OK ' if present else 'MISS'}]  custom field {config.PRECALL_FIELD_LABEL!r}")
    ok = ok and present
    for label in config.POSTCALL_FIELD_LABELS.values():
        print(f"  [{'OK ' if label in idx else 'MISS'}]  custom field {label!r}")

    for name, uuid in config.BOARDS:
        try:
            cols = api.call(f"/api/internal/siftline/board/{uuid}/column/")
            n = len(cols.get("results") or cols.get("data") or [])
            print(f"  [OK ]  board {name} reachable, {n} columns")
        except Exception as exc:  # noqa: BLE001
            print(f"  [MISS]  board {name}: {exc}")
            ok = False

    sample = tasks.sample_task_shape(api)
    print("\n  sample existing task(s) -- confirm the create payload shape against these"
          " before trusting tasks.create_task's field names:")
    print(json.dumps(sample, indent=2)[:1200])

    print(f"\n  Wendy numbers (alternated): {config.WENDY_NUMBERS}")
    print(f"  weights: urgency={config.URGENCY_WEIGHT} quality={config.QUALITY_WEIGHT}")
    print(f"  cooldown: {config.COOLDOWN_DAYS} days | pick count: {config.DAILY_PICK_COUNT}")
    print(f"  Trestle key configured: {bool(config.TRESTLE_API_KEY)}")
    print(f"  schedule: enabled={config.ENABLED} select_hour={config.SELECT_HOUR} "
          f"tz={config.BUSINESS_TIMEZONE}")
    return 0 if ok else 1


def run(commit: bool, limit: int = 0) -> dict:
    api = siftline_kpi.Api()
    store.init()
    today_iso = _today()
    tag = config.today_tag(today_iso)
    recently_texted = scoring.recently_texted_phones()

    candidates = board.pull_candidates(api)
    picked, dropped = scoring.select_top(
        candidates, today_tag=tag, recently_texted=recently_texted, n=config.DAILY_PICK_COUNT
    )
    if limit:
        picked = picked[:limit]

    board_mix: dict[str, int] = {}
    for c in picked:
        board_mix[c["board_name"]] = board_mix.get(c["board_name"], 0) + 1

    field_idx = texting.field_index(api) if commit else {}
    precall_field_uuid = field_idx.get(config.PRECALL_FIELD_LABEL, "")

    due_iso = f"{today_iso}T17:00:00Z"
    tasks_ok = texted_ok = cooldown_held = scored = errors = 0
    detail: list[dict] = []

    for cand in picked:
        row = {
            "uuid": cand["property_uuid"], "street": cand["street"],
            "score": cand["_score"], "board": cand["board_name"],
        }
        try:
            cooling = scoring.cooldown_blocked(cand, recently_texted)
            precall_text = texting.render_precall(cand)
            row["precall_preview"] = precall_text[:80]
            row["cooldown"] = cooling

            if not commit:
                detail.append(row)
                continue

            task_res = tasks.create_task(
                api, cand["property_uuid"], config.WENDY_UUID, due_iso,
                f"Call today ({cand['board_name']}, score {cand['_score']}): {cand['street']}",
            )
            row["task"] = task_res.get("uuid") if isinstance(task_res, dict) else task_res
            tasks_ok += 1

            tier = phones.ensure_scored(api, cand)
            if tier:
                scored += 1
                row["phone_tier"] = tier

            if cooling:
                cooldown_held += 1
                row["precall"] = "held: cooldown"
            else:
                texting.write_field(api, cand["property_uuid"], precall_field_uuid, precall_text)
                texted_ok += 1
                row["precall"] = "drafted"

            tagging.add_tags(api, cand["property_uuid"], [tag])
        except Exception as exc:  # noqa: BLE001 - one record must never abort the other 34
            errors += 1
            row["error"] = str(exc)[:300]
            log.exception("record %s failed", cand["property_uuid"])
        detail.append(row)

    if commit:
        batch.save(today_iso, picked)

    return {
        "date": today_iso, "candidates": len(candidates), "picked": len(picked),
        "board_mix": ", ".join(f"{k} {v}" for k, v in board_mix.items()) or "none",
        "tasks_ok": tasks_ok, "precall_drafted": texted_ok, "cooldown_held": cooldown_held,
        "cooldown_days": config.COOLDOWN_DAYS,
        "phones_scored": scored, "errors": errors, "detail": detail,
        "dropped_sample": dropped[:5], "committed": commit,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Daily 35 -- 08:00 select + assign")
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
    print(json.dumps(
        {k: v for k, v in summary.items() if k not in ("detail", "dropped_sample")}, indent=2
    ))
    print("\npicks:")
    for row in summary["detail"]:
        print(" ", json.dumps(row))
    if summary["dropped_sample"]:
        print("\nsample of excluded candidates:")
        for row in summary["dropped_sample"]:
            print(" ", row.get("property_uuid"), row.get("_exclude_reason"))

    if commit:
        from . import slack

        slack.post_select_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
