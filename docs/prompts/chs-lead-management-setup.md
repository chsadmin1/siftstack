# Creative Home Solutions: DataSift Lead Management Setup

Paste everything below the line into Claude Cowork after the steps in "Before you paste" are done.
Fill in every `____` in the decisions block first. Defaults marked (recommended) can stay as they are.

## Before you paste

1. Install the plugin from `C:\Users\djpkc\siftstack-chs\dist\datasift-lead-management.plugin` (v0.1.8, built 2026-10-14). Do not use the copies in `Downloads` or `.claude\skills`. They carry the same version number but different contents.
2. Create the working folder `C:\Users\djpkc\CHS-Lead-Management` and link it in Cowork. It must be outside the `siftstack-chs` repo. That repo is public, and its `.gitignore` does not cover `service_account.json` or `.datasift-config.json`.
3. Copy `C:\Users\djpkc\siftstack-chs\docs\kpi\CHS-KPI-Tracking.xlsx` into the working folder.
4. Open Chrome, log into DataSift as the Creative Home Solutions account (not 525 Homes), and connect the Claude in Chrome extension.
5. Make sure every lead manager in the rotation already has a DataSift user seat with the Lead Manager role.
6. Make sure smrtPhone is connected to DataSift, and every lead manager has a smrtPhone user and number. CHS uses smrtPhone for both calls and texts.

---

You are setting up the Creative Home Solutions (CHS) DataSift account for lead management. CHS buys houses in Ada and Canyon counties, Idaho. All times are Mountain (America/Boise). CHS uses smrtPhone for both calls and texts, through DataSift's smrtPhone integration. Use the installed `datasift-lead-management` plugin (v0.1.8) for the custom fields, the training manual and the KPI bot walkthrough. The round-robin task presets are not in the plugin; build them as described in Phase 4.

## Decisions (filled in by me)

```
COMPANY_NAME:          Creative Home Solutions
TRAINER_NAME:          ____            (the person leading the team; goes into the manual's scripts)
LEAD_MANAGERS:         ____            (first name + DataSift login email for each, e.g. Nick <nick@...>)
ROUND_ROBIN_SCOPE:     call-new-lead-only (recommended) | all-lead-management-presets
FOLLOW_UP_OWNER:       record-assignee (recommended) | round-robin
SKIP_WEEKENDS:         ____            (yes/no, for the Call New Lead task)
PHONE_PROVIDER:        smrtPhone       (calls and texts)
WEBSITE_LEADS:         ____            (no, or platform + smrtPhone inbox name, e.g. Carrot + "Online Inbounds")
PPL_LEADS:             ____            (no, or provider + smrtPhone inbox name)
DIRECT_MAIL_INBOX:     ____            (smrtPhone inbox name, e.g. "Inbound Leads")
COLD_CALLING:          yes
KPI_REPS:              ____            (names exactly as reps will type them in the KPI sheet)
KPI_BOT_HOST:          github-actions (recommended) | cowork-local
MANUAL_BRANDING:       datasift-template | chs-logo-and-colors (needs logo file + 2 hex colors in the working folder)
SLACK_CHANNEL:         #sales-performance, channel ID C0C74BAS5E3
```

## Ground rules

- **Account check before any DataSift write.** In the DataSift tab, decode the session token and show me the email and account it belongs to:
  `JSON.parse(atob(localStorage.getItem('rs_token').split('.')[1].replace(/-/g,'+').replace(/_/g,'/')))`
  Wait for me to confirm it is the CHS account. This machine also works on the 525 Homes account, so never assume.
- **Read first, then write.** Each phase starts with a read-only check and a short plan. Wait for my "go" before any write.
- **Log every object you create** (custom field group, custom field, task preset, sequence) with its ID to `setup-log.json` in the working folder, so anything can be rolled back.
- **Secrets stay out of chat and out of git.** Never write a webhook URL, service account key or token into any file outside the working folder.
- **Follow the plugin's own "Do NOT" rules**, with the overrides written below. If a step fails, stop and show me the exact error.

## Phase 0: Read-only inventory

Using the token and headers from the build-custom-fields skill (base URL `https://apiv2.reisift.io`), read and report as tables:

1. **Custom fields.** `GET /api/internal/custom-fields/group/` and `GET /api/internal/custom-fields/?limit=999`. List any existing group or field whose label matches one of the 7 groups or 75 fields in the plugin's `field-definitions.json`. Note any match with a different field type.
2. **Users.** `GET /api/internal/account/user/?offset=0&limit=999`. Show each LEAD_MANAGERS person's uuid, role and active flag. Flag anyone missing or not in the Lead Manager role.
3. **Task presets.** `GET /api/internal/task-group/?offset=0&limit=999`, then `GET /api/internal/task-group/{uuid}/task-preset/?offset=0&limit=999` for each group. Show every Lead Management preset with its current assignee, round-robin flag, deadline and skip-weekends setting.
4. **Sequences.** In the DataSift UI, list every sequence triggered by a status change to New Lead, and what each one does.
5. **smrtPhone.** In DataSift's integration settings, confirm smrtPhone is connected. List each lead manager's smrtPhone number if DataSift shows it, and flag anyone without one. Ask me for the exact smrtPhone inbox names if any are still blank in the decisions block.
6. **Plugin integrity.** Confirm `personalize.py` contains `_FILE_COMPLETE`.

Stop and wait for my decisions on any collisions before Phase 1.

## Phase 1: Personalize (setup-my-training)

Run the `setup-my-training` skill. Use the answers from the decisions block and only ask me for anything still blank. For phone provider, use PHONE_PROVIDER. Show the summary table, wait for my yes, then write `.datasift-config.json` to the working folder.

## Phase 2: 75 custom fields (build-custom-fields)

Run the `build-custom-fields` skill, with these overrides:

1. **Skip anything Phase 0 found** unless I said otherwise.
2. **Canary first.** Create one group and one dropdown field, then read both back before the batch. If the plugin's group path (`/custom-fields/groups/` with `name`) returns 404 or 400, use `POST /api/internal/custom-fields/group/` with `{"label": <name>, "entity_type": "property"}`. That is the path and body the CHS repo's own code uses.
3. **Dropdown options carry a value.** Send each option as `{"label": o, "value": o}`. Confirm on the canary that all options came back.
4. Run the rest as one batch in the browser with the 150 ms delay, as the skill says.

Verify the final state by reading it back, not from the POST results:

| # | Group | Fields |
|---|---|---|
| 1 | Qualifying Questions | 8 |
| 2 | Property Condition - General | 10 |
| 3 | CapEx Assessment | 17 |
| 4 | Property Debts & Encumbrances | 17 |
| 5 | Title & Ownership | 8 |
| 6 | Deal Intelligence | 7 |
| 7 | Appointment & Next Steps | 8 |
| | **Total** | **75** |

Also confirm the type mix: 43 dropdown, 15 single-line, 10 number, 5 multi-line, 2 date.

## Phase 3: Training manual (generate-training-manual)

Run the `generate-training-manual` skill exactly as written. Use `python3`, or `python` if `python3` is missing. Then verify:

- Both files exist at the sizes the skill lists.
- Neither file still contains "Florida Cash", "FCRE" or "Tyler".
- "Creative Home Solutions" and TRAINER_NAME appear in both.
- The inbox references read as smrtPhone plus the CHS inbox names from the decisions block.

If MANUAL_BRANDING is chs-logo-and-colors, leave the plugin's outputs untouched. Make copies named `CHS-Lead-Manager-Training.html` and `CHS-Lead-Manager-Training-Manual.docx`, and change only the logo and the brand color variables in the copies. Show me both before calling this done.

## Phase 4: Round-robin task presets (not in the plugin)

How DataSift handles this:

- Task presets live under Events > Configure Presets. Assignment can be one user, a role, Users Round-Robin or Role Round-Robin. Round-robin spreads tasks evenly across the selected users.
- Every default preset starts assigned to the account owner (Sensei). Until that changes, all lead work lands on one person.
- Lead Managers can open records assigned to anyone, but not unassigned records. A new lead must be assigned to a person, or the task points at a record the lead manager cannot open.
- A preset does nothing on its own. A sequence has to create the task.

Build:

1. **Call New Lead.** Set Users Round-Robin across LEAD_MANAGERS, deadline same day, skip weekends per SKIP_WEEKENDS. If ROUND_ROBIN_SCOPE is all-lead-management-presets, apply the same assignment to No Contact New Lead, Nurture New Lead, Hot, Warm and Cold Follow-Up.
2. **Follow-up presets.** If FOLLOW_UP_OWNER is record-assignee, a seller should keep the same lead manager after the first call. Check whether the Create New Task action can assign to the record's assignee. If it can, use that. If it cannot, stop and give me the options before changing anything.
3. **Reassign** any remaining Lead Management preset away from the Sensei, per my answers.
4. **New Lead sequence.** Trigger: status changes to New Lead. Actions, in order: Assign Property (round-robin across the same LEAD_MANAGERS), Clear Property Tasks, Create New Card on Board "Lead Management", phase "New Lead", Create New Task "Call New Lead" with "Assign this task to the property" on. If Phase 0 found an existing New Lead sequence, update it instead of adding a second one. Two sequences would create two tasks per lead.
5. Make preset and sequence changes in the DataSift UI through Chrome. Use the API only to read back. If you must create a preset by API, use `POST /api/internal/task-group/{group_uuid}/task-preset/`. Send exactly one of `assigned_to_users`, `assigned_to_role` or `assigned_to_user`, and leave the other two out entirely. An empty list or two non-null keys returns 400.
6. **Read back** each preset and confirm `round_robin: true` and the right user uuids.
7. **Live test.** Create one test record per lead manager, with "TEST RR" in the address. Do not tag them P1 or Autotext. Set each to New Lead and confirm that each lead manager gets one record and one Call New Lead task. Confirm the record's assignee and the task's assignee are the same person on every test record. If they differ, report it and stop. Delete the test records and their tasks after I confirm.

## Phase 5: Daily KPI Slack bot

CHS already has a CHS-specific copy of the plugin's KPI bot in its GitHub repo, built but not yet running. It is scheduled weekdays at 5 PM Mountain with daylight saving handled, posts per-rep numbers and names who has not logged, and reads the CHS KPI sheet. Do not run two bots into the same channel.

### If KPI_BOT_HOST is github-actions

Use the plugin's `setup-kpi-bot` walkthroughs (`google-cloud-setup.md`, `slack-setup.md`) for the click-by-click steps, one step at a time. Do not create the local `kpi-bot/` scheduled task.

1. **KPI sheet.** I import `CHS-KPI-Tracking.xlsx` from the working folder into Google Sheets and name it "Creative Home Solutions KPIs" (the manual refers to it by that name). Use the CHS template, not the plugin's `KPI-Tracking-Template.xlsx`, because the CHS bot matches the CHS headers exactly.
2. **Google service account.** Walk me through it, then share the sheet with the service account's `client_email` as Viewer.
3. **Slack webhook.** In the CHS Slack app, Incoming Webhooks > Add New Webhook > #sales-performance. A webhook is bound to one channel when it is created, so the channel ID is not part of its URL.
4. **GitHub.** I enter these myself at github.com/chsadmin1/siftstack > Settings > Secrets and variables > Actions. Do not ask me to paste the values to you.
   - Secret `SLACK_SALES_WEBHOOK_URL`: the webhook from step 3
   - Secret `GOOGLE_SERVICE_ACCOUNT_KEY`: the JSON key, base64-encoded with PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("key.json"))`
   - Secret `KPI_SPREADSHEET_ID`: the ID between `/d/` and `/edit` in the sheet URL
   - Variable `KPI_REPS`: KPI_REPS
5. **Test.** I run Actions > "Daily KPI digest" with dry run checked and paste you the log. Then I run it without dry run.
6. **Confirm the post landed in channel C0C74BAS5E3.** Read the channel if a Slack connector is available; otherwise ask me.
7. Tell me that the daily call coaching job posts to the same channel through the same webhook secret. It grades smrtPhone calls that the SMS agent reads from smrtPhone's call log. It starts posting once a smrtPhone login has been uploaded with the "Upload smrtPhone session" workflow, and its own Google and OpenRouter setup is done (`docs/setup/chs-coaching-and-kpi.md`).

### If KPI_BOT_HOST is cowork-local

Run the `setup-kpi-bot` skill as written, with the plugin's template. Point the webhook at #sales-performance, set `KPI_COMPANY_NAME=Creative Home Solutions`, and schedule `0 17 * * 1-5` in Mountain time. Warn me that it only runs while this computer is on and Cowork is open. Leave the GitHub KPI secrets unset so the GitHub job never double-posts.

### Either way

Tell me who logs what. Reps add one row per day to their role's tab: First to Market for cold callers and texters, Lead Management for lead managers, Acquisition for acquisitions. Dials, new leads and talk time from smrtPhone's dialer reports go in the Dialer tab of the CHS sheet, or the SmrtDialer tab of the plugin's sheet. The digest reports only what is in the sheet at 5 PM.

## Phase 6: Wrap-up

Write `SETUP-REPORT.md` in the working folder with:

- What was built, with counts and IDs, read back from DataSift.
- File paths for the manual and config.
- Anything skipped, failed or left for me, each with the one action that finishes it.
- Whether smrtPhone's A2P 10DLC texting registration is approved. Until it is, texts the manual teaches lead managers to send will not deliver.

Then give me the same summary in chat.
