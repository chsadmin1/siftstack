# CLAUDE.md — SiftStack (Creative Home Solutions)

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**SiftStack** is a full-stack real estate investing operations platform built around the DataSift.ai (REISift) CRM. This repository is Creative Home Solutions' own copy, forked from a larger internal codebase. It contains the full platform's source code, but **only one subsystem is currently configured and deployed for this account: the P1 List Manager** (see below). Everything else in `src/` is present but inert — no credentials, no deployment, no Fly app — until it's specifically set up for this account.

## P1 List Manager (the live subsystem)

`src/p1_manager/` automates the DataSift County List Playbook's P1 (Priority 1) marketing lists:

- **Weekly** (Saturdays): finds records newly added to existing P1 presets during the past week, tags them, and posts a Slack digest asking whether/where they should be pushed onto a SiftLine board + phase.
- **Monthly** (the 28th): re-checks the County List Playbook for newly-qualifying P1 combinations, creates new presets for them, fully re-pulls every P1 preset's records, tags everything, exports a CSV, and posts a month-over-month change report.
- Every record pulled gets three tags: a bare `P1`, a `Last Pulled MM-DD-YYYY`, and a composite `P1 | MMYY | <List Name>` traceability tag.

Deployed as the Fly.io app `chs-p1-manager` (`fly.chs-p1-manager.toml`), driven by `src/p1_manager/schedule.py` inside the always-on container. A Slack App (bot token + signing secret) handles the weekly ask / reply loop; see `deploy/sync_p1_manager_secrets.py` for the required environment variables.

```bash
python src/p1_manager/weekly.py --dry-run     # preview this week's new records, no writes
python src/p1_manager/monthly.py --dry-run    # preview this month's refresh, no writes
```

Deployment is via GitHub Actions (`.github/workflows/deploy-chs-p1-manager.yml`), not local `flyctl` — see that file's header comment for why.

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
