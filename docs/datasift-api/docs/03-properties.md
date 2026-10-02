# Properties

The property record is the core object in DataSift: an address, its owner, statuses, tags, lists, phones, custom fields, notes, documents, and marketing history. Base path: `/api/internal/property/` (an equivalent surface exists under `/api/internal/properties/property/`; both are listed in the endpoint index).

## Core CRUD

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/property/` | List records (paginated; returns `count` + `data`) |
| POST | `/api/internal/property/` | Create one record |
| POST | `/api/internal/property/bulk-create/` | Create many records in one request |
| POST | `/api/internal/property/exists/` | Check existence by `reapi_id` or `sift_id` before creating |
| GET | `/api/internal/property/{uuid}/` | Full record detail |
| PATCH | `/api/internal/property/{uuid}/` | Partial update |
| DELETE | `/api/internal/property/{uuid}/` | Delete (destructive; verify intent first) |
| GET | `/api/internal/property/autocomplete/` | Address autocomplete within the account |
| GET | `/api/internal/property/neighborhoods/` | Distinct neighborhoods present in the account |
| POST | `/api/internal/property/address-info/` | Resolve address info for a payload |
| GET | `/api/internal/property/{uuid}/logs/` | Per-record change log |
| GET | `/api/internal/property/{uuid}/next/`, `.../prev/` | Walk records in list order |

## Creating records

Minimal create - `address` is the only required object:

```bash
curl -X POST https://apiv2.reisift.io/api/internal/property/ \
  -H "Authorization: Api-Key YOUR_OPEN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "address": {
      "street": "123 Main St",
      "city": "Knoxville",
      "state": "TN",
      "postal_code": "37902",
      "country": "US"
    },
    "owner": {
      "first_name": "Jane",
      "last_name": "Seller",
      "phones": [],
      "emails": []
    },
    "lists": "Foreclosure 2026-07",
    "tags": "FTM",
    "notes": "Imported from courthouse pull 2026-07-20"
  }'
```

The create payload also accepts `status` (object) and `assigned_to` (team member email). The response returns the new record with its `uuid` and a `type` of `clean` or `incomplete` depending on validation.

For imports of more than a handful of records use `bulk-create` with an array of the same payload shape, then verify: query `GET /api/internal/property/?limit=1` before and after and compare `count`, and spot-read two or three records by searching their addresses.

**Dedupe first.** Call `POST /api/internal/property/exists/` per record (or search by address) before creating; re-imports of the same address create duplicate records, and cleanup afterward is far more expensive than the check.

## Record actions

These per-record action endpoints drive day-to-day operations:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/internal/property/{uuid}/add-tags/` | Add tags to the record |
| POST | `/api/internal/property/{uuid}/remove-tags/` | Remove tags |
| POST | `/api/internal/property/{uuid}/add-lists/` | Add the record to lists |
| POST | `/api/internal/property/{uuid}/remove-lists/` | Remove from lists |
| POST | `/api/internal/property/{uuid}/status/` | Set the record's status |
| POST | `/api/internal/property/{uuid}/add-notes/` | Append a note |
| POST | `/api/internal/property/{uuid}/assign/` | Assign to a team member |
| POST | `/api/internal/property/{uuid}/hotness/` | Set hotness (lead temperature) |
| POST | `/api/internal/property/{uuid}/do-not-mail-ever/` | Permanent mail suppression |
| POST | `/api/internal/property/{uuid}/add-phone-tag/` | Tag a phone on the record (see `04-owners-and-phones.md`) |

> **Schema note:** in the generated reference, several action endpoints display the generic property payload as their request body. In practice each action takes a small payload naming just the thing it changes (for example, the tags to add, or the status to set). Build against the specific action, send the minimal payload, and verify the change with a read; if a payload is rejected with a 400, the validation message names the expected field.

## Marketing attempt counters

Each record tracks attempt counters per channel, incremented through dedicated endpoints. These power cadence and suppression logic (see the playbooks):

| Method | Path | Channel |
|---|---|---|
| POST | `/api/internal/property/{uuid}/sms-attempts/` | SMS |
| POST | `/api/internal/property/{uuid}/rvm-attempts/` | Ringless voicemail |
| POST | `/api/internal/property/{uuid}/predictivecall-attempts/` | Predictive dialing |

Owner-level equivalents exist under `/api/internal/owner/{uuid}/` including `skiptrace-attempts`.

## Skip tracing

```
POST /api/internal/property/skip-trace/
```

Submits property records for skip tracing under your plan's skip trace capability. Skip tracing runs asynchronously: verify by re-reading the record and checking `skiptraced`, `has_phones`, and the phone list on the owner, and watch aggregate progress with the skip trace stats endpoints in `09-activity-and-reporting.md`.

## Documents and images

Files attach per record. Upload flows use presigned URLs: request the presigned URL, PUT the binary to it, then register the document or image.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/property/{property_uuid}/document/` | List documents |
| GET | `/api/internal/property/{property_uuid}/document/presigned-url/` | Get an upload URL |
| POST | `/api/internal/property/{property_uuid}/document/` | Register an uploaded document |
| DELETE | `/api/internal/property/{property_uuid}/document/{uuid}/` | Remove a document |
| GET | `/api/internal/property/{property_uuid}/image/` | List images |
| GET | `/api/internal/property/{property_uuid}/image/presigned-url/` | Get an upload URL |
| POST | `/api/internal/property/{property_uuid}/image/` | Register an uploaded image |
| POST | `/api/internal/property/{property_uuid}/image/{uuid}/set-cover/` | Set the cover image |

## Custom field values

Read a record's custom field values with `GET /api/internal/property/{property_uuid}/custom-field/` and write them with `PATCH /api/internal/property/{property_uuid}/custom-field/update-values/`. Field definitions themselves are managed account-wide; see `08-custom-fields.md`.

## Statuses

Account-wide status definitions live at `GET /api/internal/global-status/` (list) with update endpoints per status. Read the list once, cache the status uuids, and use the per-record `status/` action to move records through your pipeline. Verify a status change by re-reading the record.
