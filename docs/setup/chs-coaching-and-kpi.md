# CHS call coaching and KPI digest: setup

Two scheduled jobs post to one CHS Slack channel (for example `#sales-performance`):

| Job | When | What it posts | Code |
|---|---|---|---|
| Daily call coaching | Tue to Sat, 7:30 AM Mountain | Yesterday's graded calls per rep, with score, grade, the one fix to make next, and a link to listen | `src/call_coaching/kixie_daily_audit.py`, `.github/workflows/chs-call-coaching.yml` |
| Daily KPI digest | Mon to Fri, 5:00 PM Mountain | Each rep's logged numbers for the day, who has not logged, and the funnel, cost and expense summary | `src/kpi_bot/kpi_slack_bot.py`, `.github/workflows/chs-kpi-digest.yml` |

Both run on GitHub Actions in this repo. Nothing new is deployed to Fly except one small change to `chs-sms-agent` (a read-only call log endpoint).

## How call coaching works

1. Kixie sends an End Call webhook to `chs-sms-agent` after every call. The webhook carries the recording URL, the call length and the numbers. The agent already stores it.
2. Each morning the job reads yesterday's calls from the agent (`GET /hooks/<secret>/calls`). It keeps calls that have a recording and last at least 60 seconds (or 15 seconds with a contact disposition), and skips any call already on the sheet.
3. It randomly picks up to 3 calls per rep. Each one is transcribed by an audio model, which also notes tone and pace, and triaged as a conversation, voicemail or wrong number.
4. Real conversations are graded by Claude against the matching rubric (`skills/cold-call-coach`, `lead-manager-coach` or `closer-coach`, picked from what the call was about). Rubric rules apply: auto-fail conditions, short-call handling, and a quote behind every point.
5. The job adds one row per call to the call audit sheet and posts the summary to Slack. An auto-fail is flagged for manager review.

Cost: about $0.01 for transcription plus $0.05 to $0.15 for grading per call. At 3 calls per rep per day, that is roughly $2 to $5 per rep per month.

## One-time setup

### 1. Kixie: send End Call webhooks to the agent

Kixie > Manage > Automations > Webhooks > add **End Call** pointing at:

```
https://chs-sms-agent.fly.dev/hooks/<SMS_AGENT_WEBHOOK_SECRET>/kixie
```

This is the same URL as the SMS webhook. Also add **Call Disposition** to the same URL if you want reps' dispositions on the sheet. Call recording must be on for every rep in Kixie.

### 2. Redeploy chs-sms-agent

Run GitHub Actions > "Deploy SMS agent" on this branch so the agent gets the new `/calls` endpoint. This does not change texting behavior.

Optional: add a repo secret `SMS_AGENT_REP_NUMBERS` mapping each rep's Kixie number to their name, for example `{"+12085550100": "Nick"}`. Without it, reps are named from Kixie's caller ID name. Check the first Slack post: if a rep shows up under the seller's name or a phone number, add the map and redeploy.

### 3. Google: service account and the two sheets

In the CHS Google Workspace:

1. Go to console.cloud.google.com and create a project (for example `chs-siftstack`).
2. Enable the **Google Sheets API**.
3. Create a service account under IAM > Service Accounts. On its Keys tab, choose Add key > JSON, and keep the downloaded file safe.
4. **KPI sheet**: in Google Sheets, File > Import > upload `docs/kpi/CHS-KPI-Tracking.xlsx` > Create new spreadsheet. Share it with the service account's `client_email` as **Viewer**.
5. **Call audit sheet**: create a blank spreadsheet named "CHS Call Audit". Share it with the service account as **Editor**. Step 6 writes its header row.
6. Base64 the key, which becomes the `GOOGLE_SERVICE_ACCOUNT_KEY` secret:
   - PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("key.json"))`
   - macOS / Linux: `base64 -w0 key.json`

Each sheet's ID is the part of its URL between `/d/` and `/edit`.

### 4. Slack: one incoming webhook

Create the channel. At api.slack.com/apps, open the CHS Slack app (or create one), go to Incoming Webhooks, choose Add New Webhook, and pick the channel. Copy the URL.

### 5. GitHub secrets and variables

In Settings > Secrets and variables > Actions:

| Secret | Value |
|---|---|
| `SLACK_SALES_WEBHOOK_URL` | the webhook from step 4 (both jobs post here) |
| `GOOGLE_SERVICE_ACCOUNT_KEY` | base64 key from step 3 |
| `KPI_SPREADSHEET_ID` | KPI sheet ID |
| `CALL_AUDIT_SPREADSHEET_ID` | call audit sheet ID |
| `OPENROUTER_API_KEY` | openrouter.ai key, for transcription |
| `ANTHROPIC_API_KEY` | already set for the SMS agent |
| `SMS_AGENT_WEBHOOK_SECRET` | already set |

Variables tab (optional, not secret):

| Variable | Example | Effect |
|---|---|---|
| `KPI_REPS` | `Nick, Amy` | Reps the digest expects to log every weekday. Anyone missing is called out. Names must match the sheet's Name column. |
| `CALL_COACH_REP_ROLES` | `{"Amy": "lead_management"}` | Pin a rep to a rubric: `cold_call`, `lead_management` or `closing`. Without it, each call is routed by its content. |
| `CALL_COACH_EXCLUDE` | `Dan` | Reps whose calls are never reviewed. |

### 6. First runs

1. Actions > "Daily call coaching (Kixie)" > Run workflow with **dry run** checked. The log lists the calls it would grade. If it reports 0 calls logged, the End Call webhook from step 1 is not reaching the agent.
2. Write the call audit header row once, from a machine that has the key:
   `python src/call_coaching/kixie_daily_audit.py --init-sheet` (with `GOOGLE_SERVICE_ACCOUNT_KEY` and `CALL_AUDIT_SPREADSHEET_ID` set).
   Or paste the headers by hand from `HEADERS` in `kixie_daily_audit.py`.
3. Run the call coaching workflow again with dry run off. Check the sheet and the Slack post.
4. Actions > "Daily KPI digest" > Run workflow with **dry run** checked, then without.

## How reps log their numbers

Each rep adds one row per day to their role's tab of the KPI sheet:

| Role | Tab | Name column |
|---|---|---|
| Cold caller / texter | First to Market | Ninja Name |
| Lead manager | Lead Management | Name |
| Acquisitions | Acquisition | Name |
| Anyone (optional, from Kixie's reports) | Dialer | Caller |

Use the same spelling of your name every day, and a date like `10/07/2026`. Blank cells count as 0. The 5 PM digest reads what is there at that time. Rows added later show up in the next day's week-to-date totals.

## Troubleshooting

- **"could not reach the SMS agent call log"** in Slack: the agent is down or the secret differs. Check `https://chs-sms-agent.fly.dev/health`. If `SMS_AGENT_ALLOWED_IPS` is set on the agent, GitHub's runners are blocked; leave it unset.
- **PERMISSION_DENIED** writing the call audit sheet: it is not shared with the service account as Editor.
- **A KPI section shows all zeros**: a header on that tab was renamed. The bot matches header text exactly, apart from leading and trailing spaces. `docs/kpi/KPI-REFERENCE.md` lists the expected headers.
- **Calls graded under the wrong rep**: set `SMS_AGENT_REP_NUMBERS` (step 2).
