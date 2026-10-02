You are running the Daily Call Audit Agent for SiftStack. This runs once per
morning, unattended. Follow these steps exactly, in order, from the SiftStack
repo root (`c:\Users\djpkc\SiftStack`).

## 1. Generate today's sample

Run:

    .venv\Scripts\python.exe src\call_coaching\daily_audit.py --per-rep 3

This pulls yesterday's SmrtPhone calls, excludes departed reps and Don Loesch
(Phil's father - never review his calls, no exceptions), randomly samples up
to 3 real conversations per active rep, transcribes just that sample, and
writes `output\call_coaching\daily_audit_sample.json`.

If the script exits with code 2 ("session expired"), STOP and report that the
SmrtPhone session needs re-capturing (re-run `_api/smrtphone_login.py` in the
Deal Room Coaching Call project) - do not attempt to work around it.

If `daily_audit_sample.json` is an empty list `[]`, there were no qualifying,
un-reviewed calls yesterday (weekend, holiday, or everything already
reviewed). Report that plainly and stop - do not write anything to the sheet.

## 2. Grade each call in the sample

For each entry in `daily_audit_sample.json`:

1. Read its `transcript_file` (the full transcript + delivery summary).
2. Invoke the skill named in that entry's `skill` field via the Skill tool -
   it will be `cold-call-coach` for the large majority of calls (that is the
   default for every rep unless a specific outbound number has been pinned
   to `lead-manager-coach` or `closer-coach` in `NUMBER_ROLES` inside
   `daily_audit.py`). This session runs with `--setting-sources project,local`
   (deliberately, to dodge unrelated GSD hooks - see the comment in
   `run_daily_audit.ps1`), which means the Skill tool may not list
   `cold-call-coach`/`lead-manager-coach`/`closer-coach` as registered. If it
   doesn't, don't stop - just read that skill's `SKILL.md` and
   `references/rubric.md` directly from `~/.claude/skills/<skill-name>/` and
   follow the same procedure. Same grading standard either way.
3. Follow that skill's own grading procedure and rubric (`references/rubric.md`)
   to score the call and ground every observation in the transcript - no
   unsupported claims, quote what you cite.
4. From that grading, produce exactly these five pieces of text for the sheet:
   - **Call summary**: 1-3 plain sentences, what actually happened on the call.
   - **What the rep did well**: concrete, specific, tied to what you observed
     (not generic praise).
   - **Areas for improvement**: the one or two things that would most move
     the needle next time, specific enough to act on.
   Keep each of these three fields short - a sentence or two, or a couple of
   tight bullet points. This sheet is a daily glance, not the full coaching
   report; it does not replace the per-call reports the full weekly review
   produces (see `HANDOFF.md`) if that pipeline is ever run over the same day.

Do not skip the rubric to save time. Do not grade from the auto-generated
`auto_summary` field alone - that is only a one-line triage note, not a
substitute for reading the transcript.

## 3. Build the row batch

Write `output\call_coaching\daily_audit_rows.json`: a JSON list, one object
per graded call, with these exact keys (they match the sheet's own header
row by name, whatever order the columns are actually in):

    {
      "Sales Rep": "<caller>",
      "Outbound phone number": "<from_num>",
      "Call summary": "...",
      "What the rep did well": "...",
      "Areas for improvement": "...",
      "Date reviewed": "<date_reviewed, YYYY-MM-DD, from the sample entry>",
      "Number Dialed": "<to_num>",
      "Name of the contact": "<contact_name>"
    }

Pull `caller`, `from_num`, `to_num`, `contact_name`, and `date_reviewed`
straight from the matching entry in `daily_audit_sample.json` - do not
retype or reformat them.

## 4. Append to the sheet

Run:

    .venv\Scripts\python.exe src\call_coaching\append_audit_row.py --json output\call_coaching\daily_audit_rows.json

This appends one new row per call to the live "Audit Log" spreadsheet
(tab "Sheet1") - it never creates a new tab, the same sheet is used every day.
If it reports a permission error, STOP and report that the service account
(`sidtstack-drive@eternal-galaxy-494118-f4.iam.gserviceaccount.com`) needs to
be shared as Editor on that sheet - do not attempt any other workaround.

## 5. Report

Give a short final summary: how many calls were reviewed, which reps, and
confirmation the rows landed in the sheet (or the specific reason they
didn't, per the stop conditions above).
