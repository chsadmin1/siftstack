"""chs_daily_audit.py - unattended daily call coaching on CHS's smrtPhone calls.

The no-human-in-the-loop version of daily_audit.py + DAILY_AUDIT_PROMPT.md:

  1. Pull yesterday's calls from the CHS SMS agent (GET /hooks/{secret}/calls,
     see src/sms_agent/call_log.py). The agent reads smrtPhone's call log
     through the login saved on its volume, and each row carries a direct
     recording URL on rec.smrtphone.io, so this runner needs no smrtPhone
     login of its own.
  2. Keep real conversations (recording + duration floor), skip calls already
     on the sheet (the sheet's "Call ID" column is the de-dup ledger, because
     the GitHub Actions runner has no disk between runs), and randomly sample
     up to --per-rep calls per rep.
  3. Transcribe + triage each one (transcribe.process, Gemini audio via
     OpenRouter), then grade the real conversations with Claude against the
     matching rubric (grade.py).
  4. Append one row per graded call to the CHS call-audit Google Sheet and
     post a summary to the CHS Slack channel.

If the agent's smrtPhone login has expired, step 1 fails and the Slack post
says so, with what to do (capture a new login with smrtphone_login.py and run
the "Upload smrtPhone session" workflow).

Env (GitHub repo secrets in CI, or .env locally):
  SMS_AGENT_URL                 e.g. https://chs-sms-agent.fly.dev
  SMS_AGENT_WEBHOOK_SECRET      the agent's webhook secret
  OPENROUTER_API_KEY            transcription
  ANTHROPIC_API_KEY             grading
  GOOGLE_SERVICE_ACCOUNT_KEY    base64 service-account JSON, Editor on the sheet
  CALL_AUDIT_SPREADSHEET_ID     the CHS call-audit sheet
  SLACK_CALL_AUDIT_WEBHOOK_URL  CHS Slack incoming webhook
  CALL_COACH_TZ                 default America/Boise
  CALL_COACH_REP_ROLES          optional JSON {"rep name": "lead_management"} to pin a
                                rep to a rubric (cold_call | lead_management | closing).
                                Rep names are smrtPhone user names.
  CALL_COACH_EXCLUDE            optional comma list of rep names never reviewed

USAGE:
  python src/call_coaching/chs_daily_audit.py --init-sheet       # write the header row once
  python src/call_coaching/chs_daily_audit.py --dry-run          # show the sample only, no cost
  python src/call_coaching/chs_daily_audit.py                    # yesterday, 3 per rep
  python src/call_coaching/chs_daily_audit.py --date 2026-10-06 --per-rep 5
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import append_audit_row  # noqa: E402
import grade as grader  # noqa: E402
import pull_calls  # noqa: E402
import transcribe  # noqa: E402

OUT_DIR = pull_calls.OUT_DIR
TZ = ZoneInfo(os.environ.get("CALL_COACH_TZ", "America/Boise"))

DEFAULT_PER_REP = 3
DEFAULT_MIN_SECONDS = 60
CONTACT_DISPOSITIONS = {"correct number", "contact made", "interested", "lead", "callback"}

PIPELINE_TO_SKILL = {
    "cold_call": "cold-call-coach",
    "lead_management": "lead-manager-coach",
    "closing": "closer-coach",
}
SKILL_LABEL = {"cold-call-coach": "Cold call", "lead-manager-coach": "Lead mgmt",
               "closer-coach": "Closing"}

# The sheet's header row, written by --init-sheet. append_audit_row maps by
# header name, so columns can be reordered or added on the sheet freely.
HEADERS = [
    "Date reviewed", "Call date", "Sales Rep", "Rubric", "Score", "Grade", "Auto-fail",
    "Call summary", "What the rep did well", "Areas for improvement",
    "Name of the contact", "Number Dialed", "Outbound phone number", "Direction",
    "Duration (s)", "Recording", "DataSift record", "Call ID",
]


def _env(key: str, default: str = "") -> str:
    try:
        return append_audit_row._env(key)
    except KeyError:
        return default


def target_date(date_arg: str | None) -> datetime.date:
    if date_arg:
        return datetime.date.fromisoformat(date_arg)
    return (datetime.datetime.now(TZ) - datetime.timedelta(days=1)).date()


def local_date(created_at_utc: str) -> datetime.date | None:
    try:
        dt = datetime.datetime.strptime(created_at_utc[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=datetime.timezone.utc).astimezone(TZ).date()


def fetch_calls(day: datetime.date) -> list[dict]:
    """The day's calls from the SMS agent, filtered to the local business day."""
    base = _env("SMS_AGENT_URL").rstrip("/")
    secret = _env("SMS_AGENT_WEBHOOK_SECRET")
    if not base or not secret:
        raise RuntimeError("SMS_AGENT_URL and SMS_AGENT_WEBHOOK_SECRET are required")
    start = datetime.datetime.combine(day, datetime.time.min, TZ).astimezone(datetime.timezone.utc)
    # Pad the window and filter on the call's own start time below, so a
    # clock skew between smrtPhone and the agent cannot drop a call.
    since = (start - datetime.timedelta(hours=1)).isoformat(timespec="seconds")
    until = (start + datetime.timedelta(days=1, hours=2)).isoformat(timespec="seconds")
    url = f"{base}/hooks/{secret}/calls?" + urllib.parse.urlencode({"since": since, "until": until})
    last = None
    for _ in range(3):  # Fly's edge often drops the first request after idle
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=120) as r:
                data = json.loads(r.read())
            return [c for c in data.get("calls", []) if local_date(c.get("created_at_utc")) == day]
        except urllib.error.HTTPError as e:
            # The agent answers 503 with a reason when smrtPhone cannot be
            # read. An expired login is not transient, so say it and stop.
            try:
                body = json.loads(e.read() or b"{}")
            except ValueError:
                body = {}
            if body.get("error") == "smrtphone_session":
                raise RuntimeError(body.get("detail") or "smrtPhone login is missing or expired") from None
            last = f"HTTP {e.code} {body.get('detail') or ''}".strip()
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"could not read the smrtPhone call log from the SMS agent: {last}")


def qualifies(call: dict, min_seconds: int) -> bool:
    if not call.get("recording_url"):
        return False
    dur = int(call.get("duration_seconds") or 0)
    if dur >= min_seconds:
        return True
    return (call.get("disposition") or "").strip().lower() in CONTACT_DISPOSITIONS and dur >= 15


def _rep_roles() -> dict[str, str]:
    raw = _env("CALL_COACH_REP_ROLES")
    try:
        return {k.strip().lower(): v for k, v in json.loads(raw).items()} if raw else {}
    except json.JSONDecodeError:
        print("WARNING: CALL_COACH_REP_ROLES is not valid JSON, ignoring", file=sys.stderr)
        return {}


def _excluded() -> set[str]:
    return {n.strip().lower() for n in _env("CALL_COACH_EXCLUDE").split(",") if n.strip()}


def init_sheet() -> int:
    svc = append_audit_row._build_service()
    sheet_id, tab = append_audit_row._sheet()
    if append_audit_row.get_headers(svc):
        print("Header row already present, leaving it alone.")
        return 0
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=f"'{tab}'!A1", valueInputOption="RAW",
        body={"values": [HEADERS]},
    ).execute()
    print(f"Wrote {len(HEADERS)} headers to '{tab}'.")
    return 0


def post_slack(text: str) -> None:
    url = _env("SLACK_CALL_AUDIT_WEBHOOK_URL")
    if not url:
        print("WARNING: SLACK_CALL_AUDIT_WEBHOOK_URL not set, skipping Slack", file=sys.stderr)
        return
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: Slack post failed: {e}", file=sys.stderr)


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "..."


def slack_summary(day: datetime.date, rows: list[dict], found: int, failures: int,
                  sheet_url: str) -> str:
    label = f"{day:%a %b} {day.day}"
    if not rows:
        return (f":telephone_receiver: *Call coaching, {label}*: no calls to grade "
                f"({found} calls logged, none were real conversations with a recording)."
                + (f" {failures} failed to process, see the run log." if failures else ""))

    lines = [f":telephone_receiver: *Call coaching, {label}*: {len(rows)} calls graded"]
    by_rep: dict[str, list[dict]] = {}
    for r in rows:
        by_rep.setdefault(r["Sales Rep"], []).append(r)
    for rep, rs in sorted(by_rep.items()):
        scored = [int(r["Score"]) for r in rs if str(r["Score"]).isdigit() and int(r["Score"]) > 0]
        avg = f"avg {sum(scored) / len(scored):.0f}/100" if scored else "no full-call scores"
        lines.append(f"\n*{rep}*: {len(rs)} calls, {avg}")
        for r in rs:
            score = f"{r['Score']}/100 {r['Grade']}" if str(r["Score"]) not in ("", "0") else r["Grade"]
            flag = " :rotating_light: *AUTO-FAIL, manager review*" if r["Auto-fail"] == "YES" else ""
            who = r["Name of the contact"] or r["Number Dialed"]
            rec = f" <{r['Recording']}|listen>" if r["Recording"] else ""
            lines.append(f"  - {r['Rubric']} with {who} ({score}){flag}{rec}\n"
                         f"      Fix next: {_clip(r['Areas for improvement'], 220)}")
    if failures:
        lines.append(f"\n_{failures} call(s) failed to transcribe or grade, see the run log._")
    if sheet_url:
        lines.append(f"\nFull reviews: <{sheet_url}|call audit sheet>")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily smrtPhone call coaching -> sheet + Slack")
    ap.add_argument("--date", help="YYYY-MM-DD in CALL_COACH_TZ; default yesterday")
    ap.add_argument("--per-rep", type=int, default=DEFAULT_PER_REP)
    ap.add_argument("--min-seconds", type=int, default=DEFAULT_MIN_SECONDS)
    ap.add_argument("--dry-run", action="store_true", help="list the sample; no download, cost, sheet or Slack")
    ap.add_argument("--no-slack", action="store_true")
    ap.add_argument("--init-sheet", action="store_true", help="write the header row to an empty sheet and exit")
    args = ap.parse_args()

    if args.init_sheet:
        return init_sheet()

    day = target_date(args.date)
    print(f"smrtPhone call coaching for {day.isoformat()} ({TZ.key})")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        calls = fetch_calls(day)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        if not (args.dry_run or args.no_slack):
            post_slack(f":warning: Call coaching for {day.isoformat()} could not run: {e}")
        return 1
    print(f"  {len(calls)} calls logged")

    excluded = _excluded()
    candidates = [c for c in calls if qualifies(c, args.min_seconds)
                  and (c.get("caller") or "").lower() not in excluded]
    print(f"  {len(candidates)} qualifying (recording + duration/disposition)")

    try:
        reviewed = set() if args.dry_run else append_audit_row.column_values("Call ID")
    except Exception as e:  # noqa: BLE001 - bad sheet id, not shared, no key
        print(f"ERROR reading the call audit sheet: {e}", file=sys.stderr)
        if not args.no_slack:
            post_slack(f":warning: Call coaching for {day.isoformat()} could not read the call audit "
                       f"sheet (is it shared with the service account as Editor?): {_clip(str(e), 200)}")
        return 1
    candidates = [c for c in candidates if str(c["call_id"]) not in reviewed]

    by_rep: dict[str, list[dict]] = {}
    for c in candidates:
        by_rep.setdefault(c.get("caller") or "unknown", []).append(c)

    roles = _rep_roles()
    rows: list[dict] = []
    failures = 0
    for rep, pool in sorted(by_rep.items()):
        random.shuffle(pool)
        print(f"  {rep}: {len(pool)} candidates, sampling up to {args.per_rep}")
        done = 0
        for call in pool:
            if done >= args.per_rep:
                break
            cid = str(call["call_id"])
            if args.dry_run:
                print(f"    would grade {cid} ({call.get('duration_seconds')}s, {call.get('contact_name') or call.get('to_num')})")
                done += 1
                continue

            pull_calls.REC_DIR.mkdir(parents=True, exist_ok=True)
            transcribe.TR_DIR.mkdir(parents=True, exist_ok=True)
            mp3 = pull_calls.REC_DIR / f"{cid}.mp3"
            if not (mp3.exists() and mp3.stat().st_size > 4096):
                if not pull_calls.download_recording(call["recording_url"], mp3):
                    failures += 1
                    continue
            try:
                cls = transcribe.process(call)
            except Exception as e:  # noqa: BLE001
                print(f"    transcribe failed {cid}: {e}", file=sys.stderr)
                failures += 1
                continue
            if not cls or not cls.get("worth_grading"):
                print(f"    {cid}: {(cls or {}).get('call_type', 'skipped')}, not graded")
                continue

            pipeline = roles.get(rep.lower()) or cls.get("pipeline") or "cold_call"
            skill = PIPELINE_TO_SKILL.get(pipeline, "cold-call-coach")
            context = "\n".join([
                f"rep: {rep}", f"direction: {call.get('direction')}",
                f"duration: {call.get('duration_seconds')}s",
                f"disposition: {call.get('disposition') or 'none'}",
                f"call time (UTC): {call.get('created_at_utc')}",
            ])
            try:
                g = grader.grade(Path(cls["transcript_file"]).read_text(encoding="utf-8"), skill, context)
            except Exception as e:  # noqa: BLE001
                print(f"    grade failed {cid}: {e}", file=sys.stderr)
                failures += 1
                continue
            if g.get("report_type") == "not_scored":
                print(f"    {cid}: rubric gate says not scorable ({_clip(g.get('call_summary', ''), 80)})")
                continue

            rows.append({
                "Date reviewed": datetime.datetime.now(TZ).date().isoformat(),
                "Call date": day.isoformat(),
                "Sales Rep": rep,
                "Rubric": SKILL_LABEL[skill] + (" (short)" if g["report_type"] == "short" else ""),
                "Score": str(g["score"]) if g["report_type"] == "full" else "",
                "Grade": "FAIL" if g["auto_fail"] else g.get("grade_band", ""),
                "Auto-fail": "YES" if g["auto_fail"] else "",
                "Call summary": g["call_summary"] + (f" AUTO-FAIL: {g['auto_fail_reason']}" if g["auto_fail"] else ""),
                "What the rep did well": g["did_well"],
                "Areas for improvement": g["improve"],
                "Name of the contact": call.get("contact_name") or "",
                "Number Dialed": call.get("contact_phone") or call.get("to_num") or "",
                "Outbound phone number": call.get("from_num") or "",
                "Direction": call.get("direction") or "",
                "Duration (s)": str(call.get("duration_seconds") or ""),
                "Recording": call.get("recording_url") or "",
                "DataSift record": call.get("reisift_record_url") or call.get("crm_link") or "",
                "Call ID": cid,
            })
            (transcribe.TR_DIR / f"{cid}.grade.json").write_text(json.dumps(g, indent=1), encoding="utf-8")
            done += 1
            print(f"    graded {cid}: {g['report_type']} {g['score']} {g.get('grade_band')}")

    if args.dry_run:
        return 0

    (OUT_DIR / "chs_audit_rows.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    sheet_url = ""
    if rows:
        result = append_audit_row.append(rows, notify=False)
        if not result.get("success"):
            print(f"ERROR appending to sheet: {result.get('error')}", file=sys.stderr)
            if not args.no_slack:
                post_slack(f":warning: Call coaching graded {len(rows)} calls for {day.isoformat()} "
                           f"but could not write them to the sheet: {_clip(result.get('error', ''), 300)}")
            return 1
        sheet_url = result.get("range_url") or result.get("sheet_url", "")
        print(f"Appended {result['appended']} rows -> {sheet_url}")

    if not args.no_slack:
        post_slack(slack_summary(day, rows, len(calls), failures, sheet_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
