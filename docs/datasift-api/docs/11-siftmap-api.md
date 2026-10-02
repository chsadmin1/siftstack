# SiftMap API

SiftMap is the nationwide property layer: map search, comparables, and saved map filters that can automatically feed matching properties into your account. Base URL: `https://map.reisift.io`. Same `Authorization: Api-Key` header as the core API. Official reference: [developers.datasift.ai/sift-map](https://developers.datasift.ai/sift-map/).

## Map filters

A SiftMap filter is a saved nationwide search. With `auto_add_enabled`, the platform snapshots matching properties and feeds them into your account with your chosen lists and tags applied - a standing data feed defined entirely by API.

| Method | Path | Purpose |
|---|---|---|
| GET | `/filters/` | List saved filters (offset, page_size, ordering, search, is_active params) |
| POST | `/filters/` | Create a filter |
| GET | `/filters/{id}/` | Detail |
| PATCH | `/filters/{id}/` | Update |
| DELETE | `/filters/{id}/` | Delete |
| PATCH | `/filters/{id}/favorite/` | Mark or unmark favorite (`{"is_favorite": true}`) |
| GET / PATCH | `/filters/settings/` | Account-level filter settings |

Create payload fields: `name` (required), `description`, `filter_data` (the filter definition; required), `is_active`, `auto_add_enabled`, `email_enabled`, `replace_owners_enabled`, `lists` (list names to apply), `tags` (tag names to apply), `limit` plus `limit_type` (throttle how many records auto-add per hour or other period).

### Auto-add rules the API enforces

Creating or enabling an auto-add filter runs a matching-property count server side and validates two things: the account has the SiftMap Pro capability, and the count fits within the snapshot property cap. A too-broad filter is rejected with a validation error rather than silently truncated. While a snapshot job is running, `filter_data` updates are blocked and duplicate snapshot jobs are prevented.

### The safe build pattern

1. Create the filter with `auto_add_enabled: false` and the target `filter_data`.
2. Read it back; confirm `filter_data` stored as intended.
3. Set a conservative `limit` and `limit_type`.
4. PATCH `auto_add_enabled: true`; a validation error here means the filter matches too many properties - tighten it.
5. Verify the feed: within the core API, watch the target list's `properties-count` start moving, and spot-read a few arriving records.

## Properties

| Method | Path | Purpose |
|---|---|---|
| POST | `/properties/search/` | Search nationwide by address text or a polygon of coordinates |
| GET | `/properties/detail/{property_id}/` | Full nationwide property detail |
| POST | `/properties/add-properties/` | Pull specific properties (by id list) into your account |
| POST | `/properties/add-properties-by-query/` | Pull everything matching a search or polygon into your account |

The search and detail endpoints answer for ANY US address, not just records in your account - this is the discovery layer. The add-properties endpoints are the bridge: they take discovery results and create account records from them, at which point everything in the core API (tags, presets, tasks, skip trace) applies.

## Worked example - polygon to records

```python
import os, requests
MAP = "https://map.reisift.io"
H = {"Authorization": f"Api-Key {os.environ['DATASIFT_API_KEY']}",
     "Content-Type": "application/json"}

# 1. Search a polygon (coordinates as lon/lat pairs closing the ring)
found = requests.post(f"{MAP}/properties/search/", headers=H, json={
    "polygon": [[-83.95,35.96],[-83.90,35.96],[-83.90,35.99],[-83.95,35.99],[-83.95,35.96]]
}, timeout=60).json()

# 2. Review results, then pull selected ids into the account
# requests.post(f"{MAP}/properties/add-properties/", headers=H,
#               json={"ids": [...], "lists": ["SiftMap Pull 2026-07"]}, timeout=120)

# 3. Verify in the core API: the list's properties-count equals the number pulled
```

> **Schema note:** the generated reference does not document the search and add-properties request bodies in detail. Build one working request from an app-side search you can reproduce, keep payloads minimal, and verify every pull by counting what arrived in the account.
