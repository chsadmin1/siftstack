# CLAUDE.md — SiftStack (Creative Home Solutions)

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**SiftStack** is a full-stack real estate investing operations platform built around the DataSift.ai (REISift) CRM. This repository is Creative Home Solutions' own copy, forked from a larger internal codebase. It contains the full platform's source code, but **the P1 List Manager is configured and deployed for this account, the SMS agent is being set up, and call coaching and the KPI digest run from GitHub Actions** (see below). Everything else in `src/` is present but inert — no credentials, no deployment, no Fly app — until it's specifically set up for this account.

## P1 List Manager (the live subsystem)

`src/p1_manager/` automates the DataSift County List Playbook's P1 (Priority 1) marketing lists:

- **Weekly** (Saturdays): finds records newly added to existing P1 presets during the past week, tags them, and posts a Slack digest asking whether/where they should be pushed onto a SiftLine board + phase.
- **Monthly** (the 28th): re-checks the County List Playbook for newly-qualifying P1 combinations, creates new presets for them, fully re-pulls every P1 preset's records, tags everything, exports a CSV, and posts a month-over-month change report.
- Every record pulled gets three tags: a bare `P1`, a `Last Pulled MM-DD-YYYY`, and a composite `P1 | MMYY | <List Name>` traceability tag.
- **Phone enrichment** (weekdays 7 AM and 12 PM Mountain, `src/p1_manager/enrich.py`): finds DataSift lists created since the watcher started, posts a Trestle cost preview to the P1 channel, and only scores when someone replies `approve` in that thread (`skip` passes). Each record gets its best tier as a tag (`Dial First` .. `Drop`) via the safe bulk tag call, then the quality gate (70% with phones, 18+ Dial First/Second) is reported in the thread. Approving also starts a DataSift skip trace for records never traced (that API is account-wide, so `start_skip_trace` refuses unless the plan reads as unlimited) and scores their numbers as results land, with follow-ups in the same thread. `POST /trigger/enrich?secret=...&only=List A|List B` re-previews specific lists. Each scored phone also gets its OWN tier tag (owner `upsert-phones`, full object re-sent, read back), because the SMS agent only texts a number whose phone tag is Dial First/Second; a record counts as already scored only when a phone carries a tier. This replaced the GitHub Actions job in the old `dan525homes/creativehomesolutions` repo.

- **Trestle queue** (`src/p1_manager/trestle_queue.py`, first enrichment slot of each weekday, just before the preview): records tagged `Autotext` (`P1_AUTOTEXT_TAG`) with no phone tier tag at all, or never skip traced, go on a DataSift list `Trestle - MM-DD-YYYY`, and the P1 channel is told they need Trestle to be auto-texted; the preview that follows covers that list. Already-scored low tiers and traced no-hits are only counted. A record is not re-queued for `P1_TRESTLE_REQUEUE_DAYS` (7). `P1_TRESTLE_QUEUE=0` turns it off; `POST /trigger/trestle?secret=...` runs it now.

All schedules run on `P1_TIMEZONE = America/Boise` (Mountain).

Deployed as the Fly.io app `chs-p1-manager` (`fly.chs-p1-manager.toml`), driven by `src/p1_manager/schedule.py` inside the always-on container. A Slack App (bot token + signing secret) handles the weekly ask / reply loop; see `deploy/sync_p1_manager_secrets.py` for the required environment variables.

```bash
python src/p1_manager/weekly.py --dry-run     # preview this week's new records, no writes
python src/p1_manager/monthly.py --dry-run    # preview this month's refresh, no writes
```

Deployment is via GitHub Actions (`.github/workflows/deploy-chs-p1-manager.yml`), not local `flyctl` — see that file's header comment for why.

## SMS agent: texts through smrtPhone, calls through Kixie (being set up, 2026-10)

`src/sms_agent/` (the two-way texting agent: outreach from the `Autotext` preset, reply classification, opt-outs, hot-lead handoff to Slack) is being brought up for this account as the Fly app `chs-sms-agent` (`fly.chs-sms-agent.toml`, deployed by `.github/workflows/deploy-chs-sms-agent.yml`). It starts in `SMS_AGENT_DRY_RUN=1`, on Mountain time (`America/Boise`), signing texts as Nick. Full agent docs: `src/sms_agent/README.md`.

**Split phone systems (Dan, 2026-10-14): everything about TEXTING goes through smrtPhone; everything about CALLS (call reviews, call takeover) goes through Kixie.**
- **Texting:** `SMS_AGENT_PROVIDER=smrtphone`, the provider the upstream agent was built on. Sends use smrtPhone's API (`SMRTPHONE_API_KEY`); smrtPhone's SMS webhooks go to `POST /hooks/{SMS_AGENT_WEBHOOK_SECRET}/smrtphone`. No text sends until smrtPhone's A2P 10DLC registration is approved.
- **Calling:** Kixie's End Call (and Call Disposition) webhooks go to `POST /hooks/{SMS_AGENT_WEBHOOK_SECRET}/kixie` (Kixie: Manage > Automations > Webhooks). `kixie.normalize_webhook` turns them into `callEnded` events, and `engine.handle_call_ended` pauses the texting thread when a rep has a real call with that seller, regardless of provider. The same stored events feed call coaching (`src/sms_agent/call_log.py`). Kixie's SMS webhook is not used.
- `SMS_AGENT_CALL_LOG_POLL=0` turns off the smrtPhone call-log takeover poll (calls are not in smrtPhone), and `SMS_AGENT_VM_FOLLOWUP=0` for the same reason.
- `SMS_AGENT_RECONCILE_INTERVAL=0` until a captured smrtPhone browser session (`/data/smrtphone_state.json`) is on the volume; that backstop re-reads smrtPhone's SMS log for replies whose webhook was dropped. Set it to `300` once the file is there.
- The Kixie SMS provider (`kixie.py` send path) stays in the code but is not selected here.

Taking a record out of the `Autotext` preset (removing the tag it filters on) is the off switch: the morning run only picks current members, and `worker.drain_outbox` re-checks membership (`campaign.left_cohort`, one cached sweep per 5 min) before each `seed_touch_*` sends, so that day's queued touches are cancelled too. An unreadable preset holds the touch rather than sending it.

DataSift's own smrtPhone integration logs texts and replies to each record's activity; watch for a double count of `sms_attempts` on the first live send.

`python src/sms_agent/cli.py selftest` runs 289 zero-network checks (section 13 covers the Kixie webhooks); the endpoint checks need `fastapi` from `deploy/requirements-sms-agent.txt`.

## Call coaching and KPI digest (GitHub Actions, 2026-10)

Two scheduled GitHub Actions jobs post to one CHS Slack channel (`SLACK_SALES_WEBHOOK_URL`). Setup and secrets: `docs/setup/chs-coaching-and-kpi.md`.

- **Daily call coaching** (`.github/workflows/chs-call-coaching.yml`, Tue to Sat 7:30 AM Mountain): `src/call_coaching/kixie_daily_audit.py` reads yesterday's Kixie calls from `chs-sms-agent` (`GET /hooks/{secret}/calls`, built from stored `endcall` webhooks by `src/sms_agent/call_log.py`), samples 3 per rep, transcribes (`transcribe.py`, OpenRouter), grades with Claude against `skills/*-coach/references/rubric.md` (`grade.py`), appends to the call audit sheet (`append_audit_row.py`; the sheet's Call ID column is the de-dup ledger) and posts a Slack summary. The SmrtPhone path (`pull_calls.py`, `daily_audit.py`) is upstream's and unused here.
- **Daily KPI digest** (`.github/workflows/chs-kpi-digest.yml`, weekdays 5 PM Mountain): `src/kpi_bot/kpi_slack_bot.py` reads the KPI Google Sheet reps fill in by hand (template `docs/kpi/CHS-KPI-Tracking.xlsx`) and posts per-rep numbers plus the funnel summary.

GitHub cron has no daylight saving: each workflow has an MDT and an MST trigger and a gate step that runs only the one matching Boise time. Tests: `python tests/test_chs_coaching_kpi.py`.

## The rest of the platform (present, not configured)

The broader `src/` tree includes modules for web-scraping public notices, SMS/email outreach agents, deal analysis (comps, rehab estimates, lender packages), skip-trace pipelines, and more — see individual module docstrings for details. None of these are wired up for this account. Setting any of them up means:

1. Filling in the relevant section of `.env.example` (copy to `.env`) with this account's own credentials.
2. Scrubbing/filling in `config/sms_senders.json`, `config/sms_numbers.json`, `config/slack_ids.json` with this team's own identities (placeholder values are checked in — do not use them as-is).
3. Creating a new Fly.io app + volume + GitHub Actions workflow for that subsystem, following the `chs-p1-manager` pattern above.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in only what you're actually using
```

All source files are in `src/`; imports assume `src/` is the working directory. Run scripts from the project root with `PYTHONPATH=src`, or `python src/<script>.py` directly.
