# Use-Case Playbooks

End-to-end recipes for the workflows real operators run on this API. Each playbook lists the endpoints involved, the sequence, and the verification step that proves it worked. All examples assume the headers from `01-authentication.md`; every path without a base URL is on `https://apiv2.reisift.io`.

---

## 1. Record dossier - everything about one address

**Goal:** given an address, assemble the full picture: the account record, owner, phones, custom fields, tasks, deal, and history.

1. Find the record: `GET /api/internal/property/autocomplete/?search=<address>` or list-search.
2. Pull the record: `GET /api/internal/property/{uuid}/`.
3. Owner detail and phones: `GET /api/internal/owner/{uuid}/` for each owner on the record.
4. Custom field values: `GET /api/internal/property/{uuid}/custom-field/`.
5. Open work: `GET /api/internal/task/?<filter to this record>`; deal: `GET /api/internal/property/{uuid}/deal/`.
6. History: `GET /api/internal/property/{uuid}/logs/` and the owner message board.
7. Nationwide context: SiftMap `GET /properties/detail/{property_id}/` for valuation and sale history context.

**Verify:** the dossier's record uuid, owner uuid, and address all agree with each other.

---

## 2. Bulk import with dedupe and verification

**Goal:** land an external list (courthouse pull, purchased list, scrape output) as clean records with source list and tags applied, no duplicates, and proof of arrival.

1. Normalize addresses on your side first; bad addresses create `incomplete` records.
2. Baseline count: `GET /api/internal/property/?limit=1`, save `count`.
3. Dedupe: per record, `POST /api/internal/property/exists/`; drop hits.
4. Create the source list: `POST /api/internal/list/` named for source and date (`Foreclosure 2026-07`).
5. Import: `POST /api/internal/property/bulk-create/` with `lists` and `tags` set on every record.
6. **Verify:** re-read `count` (delta must equal your deduped input), `GET /api/internal/list/{uuid}/properties-count/` (must equal the import size), and spot-read three records by address.

Never loop `create` for bulk work, and never skip step 6: batch endpoints accept requests they only partially apply.

---

## 3. Build a sequential marketing funnel

**Goal:** a folder of stage presets over a source list, where records flow from skip trace to calling to mail to deep prospecting, and dead records leave the funnel automatically. Uses `05-tags-lists-folders.md` and `06-filter-presets.md`.

1. Create the stage tag folder and tags: `01 Skip Traced`, `02 Calling`, `03 Mail Sent`, `04 Deep Prospect`, and terminal tags `Done - Contracted`, `Dead - DNC`, `Dead - No Contact`.
2. Create the preset folder: `POST /api/internal/filter-preset-folder/`.
3. For each stage, build the filter definition (stage N requires tag N-1, excludes tag N and all terminal tags), then `POST /api/internal/filter-preset/compile/` to validate, then create the preset in the folder.
4. Create the suppression preset last: any terminal tag.
5. Daily operation: workers pull from their stage preset, do the work, and the automation applies the stage tag: `POST /api/internal/property/{uuid}/add-tags/` plus the matching attempt counter (`sms-attempts`, `predictivecall-attempts`, `rvm-attempts`).
6. Attach a scheduled export to the stages that feed external tools (dialer CSVs, mail house files).

**Verify:** every preset compiles; stage counts sum to no more than the source list count; a record tagged into stage N disappears from preset N and appears in preset N+1 (test with one record before going live).

---

## 4. Done/Dead suppression sweep

**Goal:** records that reached a terminal state stop receiving marketing everywhere, permanently.

1. Define terminal tags once (playbook 3) and make every stage preset exclude them.
2. Sweep: query for records with a terminal status but missing the terminal tag, and tag them: `POST /api/internal/property/{uuid}/add-tags/`.
3. Mail suppression is its own flag: `POST /api/internal/property/{uuid}/do-not-mail-ever/` for records that must never be mailed again (and the owner-level equivalent for the person).
4. Respect `dnc` and `opt_out` on owners for phone and SMS channels.

**Verify:** the terminal tags' `properties-count` after the sweep equals before plus newly swept; then re-run every stage preset count and confirm each dropped by the overlap. A suppression sweep that changes no stage counts did nothing.

---

## 5. Phone scoring and dial tiering

**Goal:** score phone numbers with an external service, then tier them inside DataSift so callers always dial best-first.

1. Create tier phone tags once: `Phone - Tier 1` through `Phone - Tier 4`, plus `Phone - Dead`: `POST /api/internal/phone/tag/`.
2. Export the numbers to score from the relevant preset or list (scheduled export or list read).
3. Score externally (line type, activity, connected status - whatever your vendor provides).
4. Apply tiers: `POST /api/internal/phone/add-phone-tag/` per number batch (or via the property or owner add-phone-tag actions).
5. Build dial presets per tier: stage preset AND phone tag, so queues become "Stage 02, Tier 1 phones first".

**Verify:** `GET /api/internal/phone/tag/{uuid}/properties-count/` per tier; the sum across tiers plus dead should approximate the records with phones in scope. Spot-read five records and confirm the tag landed on the right number, not just the right record.

---

## 6. Batch skip trace flow

**Goal:** skip trace a stage's records and know when the data is actually usable.

1. Scope from the funnel: the `01 Skip Traced` stage preset in reverse - records on the source list without the tag.
2. Submit: `POST /api/internal/property/skip-trace/` for the batch.
3. Monitor: `GET /api/internal/activity/skiptrace/stats/` until the batch completes; the activity feed shows the run.
4. Tag completion: apply `01 Skip Traced` to the submitted records so the funnel advances.

**Verify:** sample submitted records and check `skiptraced: true` and `has_phones`; compare the batch's hit rate against your normal baseline - an abnormal hit rate means something upstream (bad addresses, wrong scope) went wrong even though every call returned 200.

---

## 7. Daily KPI snapshot

**Goal:** one scheduled job that turns the account into a daily KPI row without exporting row-level data. Uses `09-activity-and-reporting.md`.

1. Enumerate the counters that matter: total records (`count` from the property list), per-stage preset scope counts, terminal tag counts, per-tier phone tag counts, skip trace stats.
2. Read them all in one pass daily; store the snapshot with a date on your side.
3. Report deltas day over day; deltas are the KPIs (new records, stage movement, kills, phone coverage).
4. Alert on flatlines in counters that should always move.

**Verify:** the job's first week of output against the app's own dashboards (`GET /api/internal/dashboard/{slug}/`); they must agree before anyone trusts the report.

---

## 8. SiftLine pipeline QA audit

**Goal:** a scheduled audit that catches deals rotting in columns and cards skipping required work. Uses `07-tasks-and-siftline.md`.

1. `GET /api/internal/siftline/board/` and pick the boards to audit; board detail lists columns.
2. Per column: `GET /api/internal/siftline/board/column/{column_uuid}/card/`.
3. Per card: `GET .../card/{uuid}/timeline/` for time-in-column; fetch the card's property tasks and check the column's required tasks are complete.
4. Flag: cards over the column's age threshold, cards that advanced with incomplete tasks, cards with no activity since arrival.
5. Deliver the flags as tasks (`POST /api/internal/task/`) assigned to the card owner, or as a report.

**Verify:** the audit's own output - every flagged card uuid must re-fetch and still show the flagged condition (this guards against auditing stale pages).

---

## 9. SiftMap standing feed

**Goal:** a nationwide filter that continuously feeds matching properties into the funnel with source list and entry tags pre-applied. Uses `11-siftmap-api.md`.

1. Build and validate the filter with `auto_add_enabled: false`; read back `filter_data`.
2. Set `lists` and `tags` on the filter to the funnel's source list and entry tag, and a conservative `limit`.
3. Enable auto-add; handle the validation error path (too many matches) by tightening the filter.
4. The core-side funnel (playbook 3) picks arrivals up automatically because they land tagged.

**Verify:** the source list's `properties-count` trend for the first days; each arrival spot-checked for the entry tag; the filter's limit actually throttling (count delta per day at or under the limit).

---

## 10. Time-driven campaigns on custom fields

**Goal:** run urgency campaigns keyed to a date you store per record (auction date, court date, list expiry). Uses `08-custom-fields.md`.

1. Define the date field once (`Auction Date` in a `Foreclosure` group).
2. Write it at import time via `PATCH /api/internal/property/{uuid}/custom-field/update-values/`.
3. Build presets filtered on the field: inside 30 days, inside 7 days, past date.
4. The past-date preset doubles as cleanup scope: records whose date passed leave the active funnel (tag them out, playbook 4).

**Verify:** create one test record with a known date and confirm it appears in and ages through each preset window as expected before wiring real spend to the presets.
