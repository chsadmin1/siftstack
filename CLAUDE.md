# CLAUDE.md — SiftStack (Creative Home Solutions)

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**SiftStack** is a full-stack real estate investing operations platform built around the DataSift.ai (REISift) CRM. This repository is Creative Home Solutions' own copy, forked from a larger internal codebase. It contains the full platform's source code, but **the P1 List Manager is configured and deployed for this account, the SMS agent is being set up, and call coaching and the KPI digest run from GitHub Actions** (see below). Everything else in `src/` is present but inert — no credentials, no deployment, no Fly app — until it's specifically set up for this account.

## P1 List Manager (the live subsystem)

`src/p1_manager/` automates the DataSift County List Playbook's P1 (Priority 1) marketing lists:

- **New records** (daily 6 AM Eastern = `P1_WEEKLY_AT=04:00` Mountain since 2026-10-21, `P1_WEEKLY_DAY=daily`; the job is still called "weekly" in code): finds records newly added to existing P1 presets since the last run, tags them, puts them straight onto SiftLine board `Raw`, phase = the preset's county (`Raw / Canyon`, `Raw / Ada`; `src/p1_manager/routing.py`; records already on the Raw board are skipped), and posts that they were put there automatically. Replying in that thread with another board + phase (e.g. `Lead Management / No Contact`) MOVES them: the new card is created and the Raw card the bot made is deleted (a card someone moved to another Raw phase is left alone). If the county or phase can't be found it falls back to asking. `P1_AUTO_ROUTE=0` turns routing off; `P1_AUTO_BOARD` names the board. Each run also turns "swap owners" (`replace_owners_enabled`) on for every P1 preset, so auto-adds take DataSift's current owner and property details (new P1 presets and the monthly re-pull use it too; `P1_REPLACE_OWNERS=0` turns it off; this deliberately departs from the upstream never-overwrite-owners doctrine, Dan 2026-10-21), and posts ONE approvable Trestle offer for all of that run's new records (`enrich.preview_new_p1`).
- **Monthly** (the 28th): re-checks the County List Playbook for newly-qualifying P1 combinations, creates new presets for them, fully re-pulls every P1 preset's records, tags everything, exports a CSV, and posts a month-over-month change report.
- Every record pulled gets three tags: a bare `P1`, a `Last Pulled MM-DD-YYYY`, and a composite `P1 | MMYY | <List Name>` traceability tag.
- **Phone enrichment** (weekdays 7 AM and 12 PM Mountain, `src/p1_manager/enrich.py`): finds DataSift lists created since the watcher started whose title starts with `Trestle - ` (`P1_ENRICH_ONLY_PREFIX`; only the Trestle queue's Autotext lists are scored, Dan 2026-10-20, other new lists are marked handled and skipped; since the Trestle watcher previews its own lists the moment it makes them, this scan mostly finds nothing and its real job is the skip trace follow-ups), posts a Trestle cost preview to the P1 channel, and only scores when someone replies `approve` in that thread (`skip` passes). Each record gets its best tier as a tag (`Dial First` .. `Drop`) via the safe bulk tag call, then the quality gate (70% with phones, 18+ Dial First/Second) is reported in the thread. Approving also starts a DataSift skip trace for records never traced (that API is account-wide, so `start_skip_trace` refuses unless the plan reads as unlimited) and scores their numbers as results land, with follow-ups in the same thread. `POST /trigger/enrich?secret=...&only=List A|List B` re-previews specific lists. Each scored phone also gets its OWN tier tag (owner `upsert-phones`, full object re-sent, read back), because the SMS agent only texts a number whose phone tag is Dial First/Second; a record counts as already scored only when a phone carries a tier. This replaced the GitHub Actions job in the old `dan525homes/creativehomesolutions` repo.

- **smrtPhone login keeper** (`src/p1_manager/smrtphone_keeper.py` + `smrtphone_auth.py`, a thread started by `slack_app`, hourly 6 AM-8 PM Mountain): asks `chs-sms-agent` whether its saved smrtPhone login still reads the call log (`GET /hooks/{secret}/smrtphone-session`) and, when not, logs in to smrtPhone itself with plain HTTP as `SMRTPHONE_LOGIN_EMAIL` (chs@creativehomesolutions.org, which sees every rep's calls) and uploads the fresh login to the agent. With `SMRTPHONE_TOTP_SECRET` (the authenticator key) it makes the 2FA code itself; without it, smrtPhone texts a code and the keeper posts in the P1 channel, and the reply in that thread (pending ask kind `smrtphone_code`) finishes the login. Each problem posts once a day; an unanswered code ask repeats after 4 h. It lives here, not in the agent, because Slack hands each Socket Mode event to one connection only. `POST /trigger/smrtphone?secret=...&force=true` (Actions > "Check smrtPhone login") runs it now and returns the outcome. smrtPhone's code page was not seen when this was written (2026-10-23), so it is matched by shape and every outcome carries a `seen` summary of the page's field names for diagnosis. Tests: `python tests/test_p1_smrtphone_keeper.py`.
- **Trestle watcher** (`src/p1_manager/trestle_queue.py`, a thread started by `slack_app`, every 5 min 7 AM-8 PM Mountain): records tagged `Autotext` (`P1_AUTOTEXT_TAG`) with no phone tier tag at all, or never skip traced, go on a DataSift list `Trestle - MM-DD-YYYY` as soon as they're seen, and ONE post to the P1 channel says they need Trestle to be auto-texted and is itself the approvable cost preview (`enrich.post_batch_preview`). The list is marked handled so the scheduled scan never previews it again. Each sweep reads only records not checked recently (queued ones wait `P1_TRESTLE_REQUEUE_DAYS`, 7; others are re-checked daily). Already-scored low tiers and traced no-hits are only counted. `P1_TRESTLE_QUEUE=0` turns it off; `POST /trigger/trestle?secret=...` sweeps now.

All schedules run on `P1_TIMEZONE = America/Boise` (Mountain).

Deployed as the Fly.io app `chs-p1-manager` (`fly.chs-p1-manager.toml`), driven by `src/p1_manager/schedule.py` inside the always-on container. A Slack App (bot token + signing secret) posts the daily new-records notice and the phone-scoring previews and handles their thread replies (board + phase to move a batch, approve / skip on a preview); see `deploy/sync_p1_manager_secrets.py` for the required environment variables.

```bash
python src/p1_manager/weekly.py --dry-run     # preview new records since the last run, no writes
python src/p1_manager/monthly.py --dry-run    # preview this month's refresh, no writes
```

Deployment is via GitHub Actions (`.github/workflows/deploy-chs-p1-manager.yml`), not local `flyctl` — see that file's header comment for why.

## SMS agent: texts and calls through smrtPhone (being set up, 2026-10)

`src/sms_agent/` (the two-way texting agent: outreach from the `Autotext` preset, reply classification, opt-outs, hot-lead handoff to Slack) is being brought up for this account as the Fly app `chs-sms-agent` (`fly.chs-sms-agent.toml`, deployed by `.github/workflows/deploy-chs-sms-agent.yml`). It starts in `SMS_AGENT_DRY_RUN=1`, on Mountain time (`America/Boise`), signing texts as Nick. Full agent docs: `src/sms_agent/README.md`.

**One phone system (2026-10-23): CHS texts AND calls through smrtPhone.** Kixie was used for calls from 2026-10-14 and has been removed from the repo (provider, webhook, call log, coaching job, text-touch provider).
- **Texting:** sends use smrtPhone's API (`SMRTPHONE_API_KEY`); smrtPhone's SMS webhooks go to `POST /hooks/{SMS_AGENT_WEBHOOK_SECRET}/smrtphone`. No text sends until smrtPhone's A2P 10DLC registration is approved.
- **Calling:** smrtPhone has no webhook for a finished call. Every `SMS_AGENT_RECONCILE_INTERVAL` (300 s) the worker reads smrtPhone's call log (`call_takeover.py`, `SMS_AGENT_CALL_LOG_POLL=1`) and pauses the texting thread when a rep had a real call (60 s+) with that seller. The same log feeds call coaching through `GET /hooks/{secret}/calls` (`src/sms_agent/call_log.py`). The reconcile backstop for dropped reply webhooks runs on the same timer; `RECONCILE_INTERVAL=0` would switch off call takeover too.
- **The smrtPhone login:** both logs are only readable through a saved smrtPhone browser login at `/data/smrtphone_state.json`, not the API key. The P1 Manager's login keeper (above) renews it by itself. By hand, as a backup: capture it with `python src/call_coaching/smrtphone_login.py` (it prints the PowerShell line that copies it to the clipboard), store it as the `SMRTPHONE_SESSION` repo secret, then run the "Upload smrtPhone session" workflow (`.github/workflows/upload-chs-smrtphone-session.yml`), which posts it to `POST /hooks/{secret}/smrtphone-session` and fails if smrtPhone rejects it. Until it is on the volume, `config.missing()` lists it at startup and the sweeps log a warning. Repeat when it expires; the coaching Slack post says so.
- `call_takeover._counterparty` compares numbers as 10 digits (the pool is stored as E.164, which never matched before 2026-10-23) and falls back to the call's direction when a rep dials from a line outside the texting pool.
- `SMS_AGENT_VM_FOLLOWUP=0`: the voicemail follow-up text is available now that calls are in smrtPhone, but stays off until the team asks for it.

Taking a record out of the `Autotext` preset (removing the tag it filters on) is the off switch: the morning run only picks current members, and `worker.drain_outbox` re-checks membership (`campaign.left_cohort`, one cached sweep per 5 min) before each `seed_touch_*` sends, so that day's queued touches are cancelled too. An unreadable preset holds the touch rather than sending it.

DataSift's own smrtPhone integration logs texts and replies to each record's activity; watch for a double count of `sms_attempts` on the first live send.

`python src/sms_agent/cli.py selftest` runs 302 zero-network checks (section 13 covers the smrtPhone call log, the login upload and call takeover); the endpoint checks need `fastapi` from `deploy/requirements-sms-agent.txt`. "a send increments the Sift counter" fails when run before 9 AM Eastern, because the probe send waits for business hours.

**Secrets never go in logs.** This repo is public, and so are its GitHub Actions run logs. Both apps mask `/hooks/<secret>/` and `?secret=` in uvicorn's access log (`redact` in `receiver.py` and `slack_app.py`), and the two `fetch-chs-*-logs` workflows mask them again before printing. Before that (until 2026-10-23), fetched Fly logs leaked `P1_TRIGGER_SECRET` and the agent webhook secret into public run logs; those logs were deleted and both secrets rotated.

## Call coaching and KPI digest (GitHub Actions, 2026-10)

Two scheduled GitHub Actions jobs post to one CHS Slack channel (`SLACK_SALES_WEBHOOK_URL`). Setup and secrets: `docs/setup/chs-coaching-and-kpi.md`.

- **Daily call coaching** (`.github/workflows/chs-call-coaching.yml`, Tue to Sat 7:30 AM Mountain): `src/call_coaching/chs_daily_audit.py` reads yesterday's smrtPhone calls from `chs-sms-agent` (`GET /hooks/{secret}/calls`, read live from smrtPhone's call log by `src/sms_agent/call_log.py`, with direct rec.smrtphone.io recording URLs), samples 3 per rep, transcribes (`transcribe.py`, OpenRouter), grades with Claude against `skills/*-coach/references/rubric.md` (`grade.py`), appends to the call audit sheet (`append_audit_row.py`; the sheet's Call ID column is the de-dup ledger) and posts a Slack summary. If the agent's smrtPhone login has expired, the Slack post says so instead. Upstream's local smrtPhone path (`pull_calls.py`, `daily_audit.py`) needs a person to run it and is unused here; `chs_daily_audit.py` reuses its recording download.
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
