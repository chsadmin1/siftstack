# Build prompt: Second Skip Reactivation (weekly)

Hand this to a fresh Claude Code session in the SiftStack repo. Every uuid and
count below was verified live against the account on 2026-09-17; re-verify with
`--doctor` before trusting any of it, because boards and presets in this
account get renamed and retired (see "Traps" for one that already died).

---

## What to build

A weekly, Friday-evening pipeline that takes the records nobody can reach any
more, buys them fresh phone numbers, and puts the ones that got a genuinely
reachable number back at the front of the calling queue with an owner attached.

```
Friday evening, one run, all stages:

  pull Second Skip cohort
    -> drop entity owners, drop records inside the re-skip cooldown
    -> SmartSkip bulk order: upload, map, calculate (free), pay (billed)
    -> poll + download results
    -> diff against numbers already on the record  -> "new numbers"
    -> Trestle-score each new number
    -> record has a Dial First/Second/Third number?
         YES -> reactivate: assign, move card, task, note, stage 5 texts
         NO  -> park in a stated bucket, never silently re-queue
```

This replaces the manual weekly round-robin in
`docs/agents/sops/deals_per_door_assignment_sop.md`.

---

## Decisions already made. Do not re-litigate these.

### Cohort

| | |
|---|---|
| Source | DataSift filter preset **`Second Skip`** |
| Resolves to | board **Longer Term Follow ups** `ef81c449-e84c-4e02-bdf8-d6150ecc6460`, column **Second Skip** `663c0504-cef6-40bb-b9e8-026c10cb643c` |
| Size on 2026-09-17 | **45 records** (about $6.75 at SmartSkip's $0.15 per hit) |

Pull it through `sms_agent.crm.resolve_preset()` / `fetch_cohort()` like every
other cadence in this repo. The preset is the contract: a record moved out of
that column drops out of the batch automatically, with no second list to keep
in sync.

### Exclusions, applied before the batch is priced

1. **Entity owners (LLC / trust / corp) are dropped entirely.** SmartSkip needs
   a First and a Last name, so entities are guaranteed paid misses. Reuse the
   existing `is_entity` helper the SMS sender pool already uses. Log the count
   dropped; do not route them anywhere.
2. **The re-skip cooldown is a re-entry rule, not a timer.** A record already
   carrying a `sys_smartskip_<date>` tag is eligible again only when it has
   **re-entered** the Second Skip column **at least 14 days after** that tag's
   date. Sitting in the column untouched for 14 days does *not* re-qualify it.
   That requires enough persisted state to tell "still here" from "left and
   came back" - a small JSON state file keyed by property uuid, same shape as
   `output/exhausted_cadence_state.json`.

### Payment

- Auto-pay the saved card via `POST /bulk-skip/payment-intent`.
- **Hold for a human above $25 per batch** (about 165 records). Post the price
  to Slack either way, before paying.
- Everything before `payment-intent` is free, so always `calculate` and report
  the real price first. Never estimate it.
- **If Stripe returns a `clientSecret`, 3DS is required and no automation can
  finish it.** Slack a link to `https://app.smartskip.io/bulk-skip` asking a
  human to complete that one payment, persist the `bulkSkipId`, and make the
  next run resume from it rather than re-ordering.

### The quality gate

Trestle-score every new number. A record returns to 1st Attempt only if it has
at least one number scoring **Dial First, Dial Second or Dial Third** (41-100).
Records whose new numbers are all Dial Fourth or Drop go to a stated "no
reachable number" bucket and are reported by count. They are not quietly put
back in the call queue.

Score only numbers genuinely new to the record that carry no tier tag yet.
Never re-score. `sms_agent/daily25/phones.py` already does exactly this; reuse
it rather than writing a second Trestle client.

### Reactivation, per qualifying record

1. **Assign** round-robin across **Don, Nico, Wendy**. Juan is excluded.
   - Name-to-uuid mapping is derived from the `DEALS X <name>` filter presets
     (each one's `must.any_assigned_to` holds the uuid).
   - **The active roster is an explicit config value defaulting to
     `Don,Nico,Wendy`.** Deriving it from the presets alone would silently
     start feeding Juan the moment anyone touched his preset.
   - Verified uuids: Don `75f17a8f-c8aa-4a06-8f4a-95370bdb9e3a`,
     Nico `f569c1da-9315-4304-9e10-b8d301efd1d2`,
     Wendy `d0fc8a58-31f6-4161-ad5c-7547f76c318e`,
     Juan `071f66bf-25a6-42e3-ba2c-4b4aa0beda53` (excluded).
   - Round-robin position persists across runs. Use `crm.assign()`.
2. **Move the card** off Second Skip and onto **DEALS X DOOR**
   `e65b4f8c-9995-4d02-9666-82f9ace5c6d7`, column **1st Attempt**
   `55f2d9b3-e358-4d12-bad3-0fb4e4632198`. One record, one place.
3. **Mark the old dead numbers** so the burned ones stay flagged Wrong/Dead and
   nobody redials them ahead of the new one.
4. **Create a call task** for the new assignee, so it lands in their Due Today
   rather than only on a board. Mirror `sms_agent/daily25/tasks.py`.
5. **Post a note** naming the new number, its Trestle tier, its SmartSkip
   relationship label, and the batch date, so the caller knows why this record
   reappeared.
6. **Stage a 5-touch SMS sequence** to the new number (below).

### The 5-touch text sequence

- Spacing **day 0, 2, 5, 9, 14**.
- Queued as **`held`**. A human runs the release. Same deliberate go/no-go
  every other outreach path here uses; no AI-authored text reaches a homeowner
  unread.
- Goes through **the existing outbox** (`sms_agent.seed.build/queue` with a
  `render_fn` override and its own intent namespace, e.g. `second_skip_day_N`)
  so it inherits suppression, opt-outs, quiet hours, per-number caps, pacing,
  sticky sender and the human-voice validator. Do not build a second send path.
- **Day 0 defaults to the next business morning, not Friday night.** The run
  fires Friday evening; anchoring day 0 there pushes touches into the weekend.
  Make it a config knob and state the resolved dates in the run summary.
- Copy is a **new pool**, not a reuse of the numbered D x D drip or the
  farewell cadence. Follow `text-touch-builder`'s voice rules and run the
  `AI_TELLS` validator over every pool variant at import time: no em/en dashes,
  no semicolons, no links, no emoji, no ALL CAPS, no form-letter openers. Never
  name the list. Street line only, never the full address with zip.
- **A new number that comes back a landline cannot be texted.** Those records
  are call-only. Report the count rather than letting sends fail one by one;
  this cost 6 of 35 sends on 2026-08-31.

### Runtime

- **Standalone CLI first**, scheduled worker stage gated behind an `ENABLED`
  flag that is **off by default**. Same way `daily25` shipped: `--doctor` and a
  verified `--commit --limit 1` of every stage before the flag is flipped.
  Flipping it is the deliberate go-live step.
- Schedule: **Friday evening**, business-local, America/Chicago.
- `--dry-run` must block every CRM write, every send, and above all the
  `payment-intent` call.

---

## Reuse, do not rewrite

| Need | Use |
|---|---|
| Cohort pull from a preset | `sms_agent/crm.py` `resolve_preset` / `fetch_cohort` |
| Assignment | `sms_agent/crm.py` `assign()` (`reassert_handoff_assignment()` for repair) |
| Board / column / card paging, JWT mint | `src/siftline_kpi.py` `Api`, `load_board` |
| Trestle scoring and tier tags | `sms_agent/daily25/phones.py` |
| Task creation | `sms_agent/daily25/tasks.py` |
| Scheduled stages inside the worker | `sms_agent/daily25/__init__.py`, `worker.py::_start_daily25_threads` |
| Held-then-release outbox | `sms_agent/seed.py` `build(render_fn=...)`, `queue(intent=...)`, `release_intent()` |
| State file pattern | `src/exhausted_cadence.py` |
| SmartSkip API client | `skills/deep-prospecting-v5/scripts/smartskip_trace.py` - **promote this into a real `src/` module**; today it exists only as a distributed skill script |
| SmartSkip contract and traps | `docs/api/skip-trace.md`, `skills/deep-prospecting-v5/references/smartskip-api.md` |

---

## Traps. Each of these has already bitten this codebase.

- **`DxD Exhausted` is dead.** That preset returns 0 and its board `5f910abc`
  now **404s**, which means `src/exhausted_cadence.py` can never fire. Do not
  copy its cohort wiring. Worth fixing or retiring separately.
- **Boards get renamed and retired.** `CLAUDE.md` describes 13 boards with 10
  attempt columns and per-rep DON/NICO boards. Live today: 8 boards, 5 attempt
  columns, no per-rep boards. Resolve by preset, verify in `--doctor`, and
  treat a missing board as a loud failure rather than an empty result.
- **`GET /api/internal/property/` with a query body needs
  `x-http-method-override: GET` on a POST.** A plain POST is treated as a
  record *create* and 400s with `address/owner required`.
- **No SmartSkip credentials exist yet.** `.env` has no `SMARTSKIP_EMAIL` or
  `SMARTSKIP_PASSWORD`. `--doctor` must check for them by name and say so.
- **Unpaid SmartSkip orders are invisible.** `GET /bulk-skip` does not list an
  order that has not been paid. Persist the `bulkSkipId` the moment you get it,
  before paying, or a batch exists that you cannot find.
- **The SmartSkip wallet does not pay for bulk skip.** It bills the saved card.
  $25 once sat untouched in the wallet while a batch charged the card.
- **SmartSkip is wrong about death, and its relationship labels are coarse.**
  `Deceased` came back false for a man with a published obituary, there is no
  date-of-death column at all, and 63% of "Possible Type" values are a generic
  "Relative". Use the labels as hints in the note, never as a decision input.
- **Zero results is a failure, not a quiet success.** A run that reports
  success having reactivated nothing is the failure mode this codebase keeps
  rediscovering. Exit non-zero on an empty batch and name the stage that
  produced nothing.
- **Texting is currently paused.** `DAILY25_TEXTING_ENABLED=0` since
  2026-08-31, and `SMS_AGENT_DRY_RUN` is set in `.env`. Build the text staging,
  but do not assume sends are live.

---

## Unverified. Probe before building on it.

1. **How to move a SiftLine card between boards.** Moving a card *within* a
   board is known; moving it from Longer Term Follow ups to DEALS X DOOR may
   require delete-and-recreate. Probe the API and confirm the card keeps its
   property link before writing the reactivation step.
2. **Whether SmartSkip's result phone columns are flat or nested.** Tracerfy's
   are flat (`mobile_1`, `landline_1`, ...) and code looking for `phones[]`
   reports zero on a batch that worked. Verify against a real download.
3. **Whether Wendy taking DxD records is intended.** She has 0 DxD records
   today and is the Kansas City daily25 caller. Round-robin will start feeding
   her. Confirm before the first commit run.

---

## Deliverables

```
src/second_skip/
  __init__.py               module docstring: the stages, the schedule, the flags
  config.py                 env-driven, verified uuids pinned as constants
  cohort.py                 preset pull, entity drop, cooldown re-entry rule
  smartskip.py              promoted client: signin, upload, map, calculate, pay, poll, download
  reactivate.py             diff numbers, Trestle gate, assign, move, task, note
  texts.py                  the 5-touch pool and render_fn
  run.py                    the Friday orchestration
  selftest.py               zero-network assertions
config/second_skip_roster.json
```

Plus a `CLAUDE.md` section for this build, in the house style: what it is, the
decisions, and the traps found in production.

CLI surface, matching the house pattern exactly:

```bash
python -m second_skip.run --doctor              # creds, preset, board/column, roster, Trestle, Slack
python -m second_skip.run --dry-run -v          # full walk, zero writes, zero billing, prints the price
python -m second_skip.run --commit --limit 1    # ALWAYS verify one record end to end first
python -m second_skip.run --commit              # the real thing
python -m second_skip.run --release             # human go/no-go on the staged texts
python -m second_skip.run --status              # last batch, what reactivated, what parked
python -m second_skip.run --selftest            # assertions, no network
```

## Acceptance criteria

- `--doctor` resolves the `Second Skip` preset to a live board and column,
  names all three roster members with their uuids, and reports every missing
  credential by name rather than failing on first use.
- `--dry-run` prints the exact batch price from `calculate` and bills nothing.
- `--commit --limit 1` puts one real record through every stage, and reading it
  back in DataSift shows: new assignee, card on DxD 1st Attempt, a call task, a
  note naming the number and tier, old numbers still flagged dead, and five
  `held` rows in the outbox with the right dates.
- `selftest` asserts rather than prints, runs against a throwaway
  `SMS_AGENT_DATA_DIR`, and covers: entity drop, the 14-day re-entry rule
  including the "sat there 14 days" case that must NOT re-qualify, the Trestle
  gate boundary between Dial Third and Dial Fourth, round-robin fairness and
  persistence across runs, the $25 ceiling on both sides, the 3DS branch, the
  landline-cannot-text branch, and the full 5-touch date walk.
- Every degradation states its reason in the run summary. Nothing returns an
  empty result silently.
