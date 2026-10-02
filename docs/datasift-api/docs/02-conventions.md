# Conventions - Pagination, Errors, and Operating Rules

Cross-cutting behavior that applies across the DataSift API Core and SiftMap APIs.

## Pagination

List endpoints paginate with one of two styles, visible in each endpoint's parameters in the reference:

**limit/offset** (most DataSift Core lists):

```
GET /api/internal/property/?limit=100&offset=200
```

Response shape:

```json
{ "count": 46374, "next": "...", "previous": "...", "data": [ ... ] }
```

Some resources return the page under `results` instead of `data`; check the first page before assuming. `count` is the total matching records, so plan loops as `while offset < count`.

**offset/page_size** (SiftMap and some resources):

```
GET /filters/?offset=0&page_size=50
```

Walk pages until `next` is null. Do not fire unbounded parallel page requests; fetch sequentially or with modest concurrency and back off on 429 or 5xx responses.

## Ordering and search

Where supported, list endpoints accept `ordering=<field>` (prefix with `-` for descending) and `search=<term>`. The endpoint reference lists which resources support them.

## Errors

| Code | Meaning | What to do |
|---|---|---|
| 400 | Validation error; body explains which field | Fix the payload |
| 401 / 403 | Auth or permission problem | See `01-authentication.md` |
| 404 | Wrong path or not in this account | Check the uuid and the path |
| 409 | Conflict (duplicate, state conflict) | Read the current state, reconcile |
| 429 | Rate limited | Back off with jitter, then retry |
| 5xx | Server error | Retry with exponential backoff; if persistent, report it |

Always log the response body on non-2xx; the platform's validation messages are usually specific.

## Verify the data, never the exit code

This is the number one operational rule for building on this platform. A 200 or 201 tells you the API accepted the request, not that the outcome you intended exists. Batch operations, background enrichment, and cascading updates can all accept cleanly and land differently than you expect.

After every write, verify with a read:

- Added a tag to 500 records? Re-query the tag's `properties-count` endpoint and compare.
- Created a filter preset? Retrieve it and check the compiled filter matches your intent.
- Imported a batch? Query `count` before and after, and spot-read a few records.
- Scheduled an export? Confirm the schedule object exists and check the first artifact.

Every playbook in `13-use-case-playbooks.md` ends with its verification step.

## Idempotency and dedupe

Property imports can create duplicates if you re-send the same addresses. Before creating, check existence with `POST /api/internal/property/exists/` (matches on `reapi_id` or `sift_id`) or search by address. For bulk work, dedupe your input first and use `bulk-create` once, not `create` in a loop.

## Text quirks

Titles and names are sanitized by the platform; em dashes and some special characters are stripped from titles. Use plain ASCII hyphens in names for tags, lists, presets, boards, and tasks so the name you write is the name you read back.

## Timestamps

Datetimes are ISO 8601 strings, UTC unless stated otherwise (calendar event endpoints accept a `time_zone` field). Send `2026-07-20T15:00:00Z` style values.
