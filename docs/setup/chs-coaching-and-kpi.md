# CHS call coaching and KPI digest: setup

Two scheduled jobs post to one CHS Slack channel (for example `#sales-performance`):

| Job | When | What it posts | Code |
|---|---|---|---|
| Daily call coaching | Tue to Sat, 7:30 AM Mountain | Yesterday's graded calls per rep, with score, grade, the one fix to make next, and a link to listen | `src/call_coaching/chs_daily_audit.py`, `.github/workflows/chs-call-coaching.yml` |
| Daily KPI digest | Mon to Fri, 5:00 PM Mountain | Each rep's logged numbers for the day, who has not logged, and the funnel, cost and expense summary | `src/kpi_bot/kpi_slack_bot.py`, `.github/workflows/chs-kpi-digest.yml` |

Both run on GitHub Actions in this repo. CHS calls and texts through smrtPhone. The coaching job reads the call list from `chs-sms-agent`, which reads smrtPhone's call log for it.

## How call coaching works

1. smrtPhone has no webhook for a finished call and no public call-history API. Its web app's call log has everything coaching needs: the rep, the call length, the disposition, both numbers, the DataSift record and a direct recording link. `chs-sms-agent` reads that log through a saved smrtPhone login on its volume, the same login it uses to pause texts when a rep calls a seller.
2. Each morning the job asks the agent for yesterday's calls (`GET /hooks/<secret>/calls`). It keeps calls that have a recording and last at least 60 seconds (or 15 seconds with a contact disposition), and skips any call already on the sheet.
3. It randomly picks up to 3 calls per rep. Each one is transcribed by an audio model, which also notes tone and pace, and triaged as a conversation, voicemail or wrong number.
4. Real conversations are graded by Claude against the matching rubric (`skills/cold-call-coach`, `lead-manager-coach` or `closer-coach`, picked from what the call was about). Rubric rules apply: auto-fail conditions, short-call handling, and a quote behind every point.
5. The job adds one row per call to the call audit sheet and posts the summary to Slack. An auto-fail is flagged for manager review.

Cost: about $0.01 for transcription plus $0.05 to $0.15 for grading per call. At 3 calls per rep per day, that is roughly $2 to $5 per rep per month.

## One-time setup

### 1. smrtPhone: keep a login saved for the agent

Call recording must be on for every rep in smrtPhone.

The agent reads smrtPhone's call log through a saved smrtPhone login, and that login runs out. The P1 Manager checks it every hour from 6 AM to 8 PM Mountain and logs back in by itself (`src/p1_manager/smrtphone_keeper.py`). Its notices go to the P1 channel. It uses the **chs@creativehomesolutions.org** smrtPhone login, which can see every rep's calls.

**One-time setup:**

1. **Turn on the authenticator app for chs@ in smrtPhone,** if it isn't already. When smrtPhone shows the QR code, also scan it with an authenticator app on a team phone, so people can still log in.
2. **Copy the authenticator key.** smrtPhone shows it as text next to the QR code, often under a link like "can't scan the code?". If it only shows the QR code, ask whoever maintains this repo for a way to read the key from it without anyone seeing it.
3. **Add three repo secrets** in GitHub, under Settings > Secrets and variables > Actions:
   - `SMRTPHONE_LOGIN_EMAIL`: chs@creativehomesolutions.org
   - `SMRTPHONE_LOGIN_PASSWORD`: that login's smrtPhone password
   - `SMRTPHONE_TOTP_SECRET`: the authenticator key from step 2
4. **Run Actions > "Deploy P1 Manager".** Type the same org slug as before, and leave the Slack channel box blank.
5. **Run Actions > "Check smrtPhone login"** with "Log in again even if it works" ticked. A green check and "logged in to smrtPhone again" in the P1 channel mean it's done.

With the authenticator key saved, the robot makes its own codes, and nobody has to do anything. Without it, smrtPhone texts a code to the phone on the chs@ login. The robot then posts in the P1 channel asking for it. Reply in that thread with just the code, like `123456`, within 15 minutes. Reply `new code` if it ran out. An unanswered request is asked again after 4 hours.

**What the P1 channel messages mean:**

| Message | What to do |
|---|---|
| "logged in to smrtPhone again" | Nothing. |
| "smrtPhone needs a login code" | Reply in the thread with the code from the text message or the authenticator app. |
| "couldn't log back in" and mentions the password | The chs@ smrtPhone password changed. Update `SMRTPHONE_LOGIN_PASSWORD`, run "Deploy P1 Manager", then "Check smrtPhone login". |
| "couldn't log back in" and mentions the authenticator key | Update `SMRTPHONE_TOTP_SECRET` the same way. |
| "has no smrtPhone password saved" | Do the one-time setup above. |

**By hand, as a backup:**

1. On the office machine, from the repo root, run `python src/call_coaching/smrtphone_login.py`. A browser opens. Log in to smrtPhone by hand, including any 2FA. Wait until you see the inside of smrtPhone, then press Enter in the console.
2. The script saves the login and prints a PowerShell line. Run that line; it copies the login, base64-encoded, to the clipboard.
3. In GitHub, Settings > Secrets and variables > Actions, add or update the secret `SMRTPHONE_SESSION` and paste.
4. Run Actions > "Upload smrtPhone session". It posts the login to the agent, and fails if smrtPhone rejects it.

### 2. Redeploy chs-sms-agent

Run GitHub Actions > "Deploy CHS SMS agent" on main so the agent gets the smrtPhone call log and the login endpoint. It also turns on the 5-minute sweep that pauses a seller's texts after a rep's real call, and the backstop that recovers dropped replies. Texting itself stays in dry run. If Kixie webhooks were ever pointed at the agent, delete them in Kixie (Manage > Automations > Webhooks); the agent no longer has a Kixie endpoint.

Reps are named by their smrtPhone user name. Use those names in `CALL_COACH_REP_ROLES` and `CALL_COACH_EXCLUDE`.

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
| `ANTHROPIC_API_KEY` | already set as `CHS_ANTHROPIC_API_KEY`; either name works |
| `SMS_AGENT_WEBHOOK_SECRET` | already set as `CHS_SMS_WEBHOOK_SECRET`; either name works |

Variables tab (optional, not secret):

| Variable | Example | Effect |
|---|---|---|
| `KPI_REPS` | `Nick, Amy` | Reps the digest expects to log every weekday. Anyone missing is called out. Names must match the sheet's Name column. |
| `CALL_COACH_REP_ROLES` | `{"Amy": "lead_management"}` | Pin a rep to a rubric: `cold_call`, `lead_management` or `closing`. Without it, each call is routed by its content. |
| `CALL_COACH_EXCLUDE` | `Dan` | Reps whose calls are never reviewed. |

### 6. First runs

1. Actions > "Daily call coaching (smrtPhone)" > Run workflow with **dry run** checked. The log lists the calls it would grade. If it says the smrtPhone login is missing or expired, redo step 1.
2. Write the call audit header row once, from a machine that has the key:
   `python src/call_coaching/chs_daily_audit.py --init-sheet` (with `GOOGLE_SERVICE_ACCOUNT_KEY` and `CALL_AUDIT_SPREADSHEET_ID` set).
   Or paste the headers by hand from `HEADERS` in `chs_daily_audit.py`.
3. Run the call coaching workflow again with dry run off. Check the sheet and the Slack post.
4. Actions > "Daily KPI digest" > Run workflow with **dry run** checked, then without.

## How reps log their numbers

Each rep adds one row per day to their role's tab of the KPI sheet:

| Role | Tab | Name column |
|---|---|---|
| Cold caller / texter | First to Market | Ninja Name |
| Lead manager | Lead Management | Name |
| Acquisitions | Acquisition | Name |
| Anyone (optional, from smrtPhone's dialer reports) | Dialer | Caller |

Use the same spelling of your name every day, and a date like `10/07/2026`. Blank cells count as 0. The 5 PM digest reads what is there at that time. Rows added later show up in the next day's week-to-date totals.

## Troubleshooting

- **"smrtPhone login is missing or expired"** in the coaching post: check the P1 channel for a message from the login keeper and do what the table in step 1 says. If there is none, run Actions > "Check smrtPhone login".
- **"could not read the smrtPhone call log from the SMS agent"** in Slack: the agent is down or the secret differs. Check `https://chs-sms-agent.fly.dev/health`. If `SMS_AGENT_ALLOWED_IPS` is set on the agent, GitHub's runners are blocked; leave it unset.
- **PERMISSION_DENIED** writing the call audit sheet: it is not shared with the service account as Editor.
- **A KPI section shows all zeros**: a header on that tab was renamed. The bot matches header text exactly, apart from leading and trailing spaces. `docs/kpi/KPI-REFERENCE.md` lists the expected headers.
- **Calls graded under the wrong rep**: the rep is smrtPhone's user on the call. Fix the user's name in smrtPhone, or the role pin in `CALL_COACH_REP_ROLES`.
- **"Upload smrtPhone session" says smrtPhone rejected the login**: capture a fresh one. If a fresh login is still rejected, smrtPhone may be tying the login to the office network; tell whoever maintains the agent.
