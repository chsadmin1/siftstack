"""daily_audit.py - sample and transcribe yesterday's calls for the Daily Call Audit Agent.

Lightweight daily spot-check, distinct from the full team review in HANDOFF.md:
pulls yesterday's SmrtPhone calls, groups by rep, excludes EXCLUDED_CALLERS
(transcribe.py) - which includes Don Loesch, Phil's father, never reviewed -
then randomly samples PER_REP real conversations per active rep and
transcribes+classifies just that sample (reuses transcribe.process()).

Grading itself is NOT done here - that is Claude's job, driven by
DAILY_AUDIT_PROMPT.md, which reads the sample this script writes and applies
the right coaching skill per call.

Skill routing: default is cold-call-coach for every call. To route a specific
outbound number to lead-manager-coach or closer-coach instead, add it to
NUMBER_ROLES below (keys are the rep's outbound from_num, exactly as it
appears in call_log.json). Content-based auto-triage (transcribe.classify)
and the by-name CALLER_ROLES pin still apply first; NUMBER_ROLES is the
highest-precedence override, for when a specific LINE (not a specific rep)
should always be graded against a different rubric.

De-dup: every call this script samples (graded or not) is recorded in
daily_audit_log.json so a later run - same day or any future day - never
re-samples it. There is no Call ID column on the sheet itself, so this file
is the only record of what has already been reviewed.

USAGE (from SiftStack root, venv python):
  python src/call_coaching/daily_audit.py                 # yesterday, 3 per rep
  python src/call_coaching/daily_audit.py --date 2026-08-03
  python src/call_coaching/daily_audit.py --per-rep 5
  python src/call_coaching/daily_audit.py --dry-run        # show the sample, no download/transcribe/cost

Output:
  output/call_coaching/daily_audit_sample.json   the graded-ready sample, one entry per call
  output/call_coaching/daily_audit_log.json      cumulative de-dup record (call_id -> status)
"""
from __future__ import annotations

import argparse
import datetime
import json
import random
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # for config.py
import config  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pull_calls  # noqa: E402
import transcribe  # noqa: E402

OUT_DIR = pull_calls.OUT_DIR
LOG_PATH = OUT_DIR / "daily_audit_log.json"
SAMPLE_PATH = OUT_DIR / "daily_audit_sample.json"

DEFAULT_PER_REP = 3
DEFAULT_MIN_SECONDS = 60
DEFAULT_INCLUDE_DISPOSITIONS = {"correct number"}

# Highest-precedence skill override, by the rep's OUTBOUND number (from_num).
# Empty by default - every call grades against cold-call-coach until a
# specific line needs to be pinned to lead-manager-coach or closer-coach.
# Example: NUMBER_ROLES = {"+18165551234": "lead_management"}
NUMBER_ROLES: dict[str, str] = {}

PIPELINE_TO_SKILL = {
    "cold_call": "cold-call-coach",
    "lead_management": "lead-manager-coach",
    "closing": "closer-coach",
}


def target_date(date_arg: str | None) -> datetime.date:
    if date_arg:
        return datetime.date.fromisoformat(date_arg)
    now_local = datetime.datetime.now(ZoneInfo(config.BUSINESS_TIMEZONE))
    return (now_local - datetime.timedelta(days=1)).date()


def local_date_of(created_at_utc: str) -> datetime.date | None:
    if not created_at_utc:
        return None
    try:
        dt_utc = datetime.datetime.strptime(created_at_utc, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc
        )
    except ValueError:
        return None
    return dt_utc.astimezone(ZoneInfo(config.BUSINESS_TIMEZONE)).date()


def qualifies(call: dict, min_seconds: int, include_dispositions: set[str]) -> bool:
    if not call.get("recording_url"):
        return False
    dur = call.get("duration_seconds") or 0
    if dur >= min_seconds:
        return True
    dispo = (call.get("disposition") or "").lower()
    return dispo in include_dispositions and dur >= 15


def load_log() -> dict:
    if LOG_PATH.exists():
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    return {}


def save_log(log: dict) -> None:
    LOG_PATH.write_text(json.dumps(log, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Sample + transcribe yesterday's calls for daily audit")
    ap.add_argument("--date", help="YYYY-MM-DD (business timezone); default = yesterday")
    ap.add_argument("--per-rep", type=int, default=DEFAULT_PER_REP)
    ap.add_argument("--min-seconds", type=int, default=DEFAULT_MIN_SECONDS)
    ap.add_argument("--dry-run", action="store_true", help="show the sample, skip download/transcribe")
    args = ap.parse_args()

    day = target_date(args.date)
    print(f"Daily audit for {day.isoformat()} (business tz: {config.BUSINESS_TIMEZONE})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Pulling recent call log...")
    all_calls = pull_calls.pull_log(days=4, quiet=True)

    day_calls = [c for c in all_calls if local_date_of(c.get("created_at_utc")) == day]
    print(f"  {len(day_calls)} calls logged on {day.isoformat()}")

    qualifying = [c for c in day_calls if qualifies(c, args.min_seconds, DEFAULT_INCLUDE_DISPOSITIONS)]
    print(f"  {len(qualifying)} qualifying (recording + duration/disposition threshold)")

    excluded_names = {
        c["caller"] for c in qualifying if transcribe.is_excluded(c)
    }
    if excluded_names:
        print(f"  excluding calls from/to: {', '.join(sorted(n for n in excluded_names if n))}")
    qualifying = [c for c in qualifying if not transcribe.is_excluded(c)]

    log = load_log()
    qualifying = [c for c in qualifying if str(c["call_id"]) not in log]

    by_rep: dict[str, list[dict]] = {}
    for c in qualifying:
        by_rep.setdefault(c.get("caller") or "unknown", []).append(c)

    if not by_rep:
        print("No qualifying, unreviewed, un-excluded calls for this day. Nothing to sample.")
        SAMPLE_PATH.write_text("[]", encoding="utf-8")
        return 0

    sample: list[dict] = []
    for rep, candidates in by_rep.items():
        random.shuffle(candidates)
        graded_count = 0
        print(f"  {rep}: {len(candidates)} candidates, sampling up to {args.per_rep}")
        for call in candidates:
            if graded_count >= args.per_rep:
                break
            cid = str(call["call_id"])

            if args.dry_run:
                sample.append({**call, "skill": "cold-call-coach", "pipeline": "cold_call (dry-run, not transcribed)"})
                graded_count += 1
                continue

            pull_calls.REC_DIR.mkdir(parents=True, exist_ok=True)
            transcribe.TR_DIR.mkdir(parents=True, exist_ok=True)
            mp3 = pull_calls.REC_DIR / f"{cid}.mp3"
            if not (mp3.exists() and mp3.stat().st_size > 4096):
                ok = pull_calls.download_recording(call["recording_url"], mp3)
                if not ok:
                    log[cid] = {"status": "download_failed", "date_reviewed": day.isoformat(), "caller": rep}
                    continue

            cls = transcribe.process(call)
            if cls is None:
                log[cid] = {"status": "excluded_or_missing", "date_reviewed": day.isoformat(), "caller": rep}
                continue
            if not cls.get("worth_grading"):
                log[cid] = {"status": "not_gradeable", "date_reviewed": day.isoformat(), "caller": rep,
                            "call_type": cls.get("call_type")}
                continue

            pipeline = NUMBER_ROLES.get(call.get("from_num")) or cls.get("pipeline") or "cold_call"
            entry = {
                "call_id": call["call_id"],
                "date_reviewed": day.isoformat(),
                "caller": rep,
                "from_num": call.get("from_num"),
                "to_num": call.get("to_num"),
                "contact_name": call.get("contact_name"),
                "reisift_record_url": call.get("reisift_record_url"),
                "pipeline": pipeline,
                "skill": PIPELINE_TO_SKILL.get(pipeline, "cold-call-coach"),
                "transcript_file": cls.get("transcript_file"),
                "auto_summary": cls.get("summary"),
            }
            sample.append(entry)
            log[cid] = {"status": "graded", "date_reviewed": day.isoformat(), "caller": rep}
            graded_count += 1

        if graded_count < args.per_rep:
            print(f"    only found {graded_count}/{args.per_rep} gradeable calls for {rep} on {day.isoformat()}")

    save_log(log)
    SAMPLE_PATH.write_text(json.dumps(sample, indent=1), encoding="utf-8")
    print(f"\nSample: {len(sample)} calls -> {SAMPLE_PATH}")
    if not args.dry_run:
        print(f"De-dup log updated -> {LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
