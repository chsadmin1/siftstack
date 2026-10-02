# Custom Fields

Custom fields extend property records with your own structured data: auction dates, equity figures, lead scores, campaign metadata, anything your workflow needs to store per record. Definitions are account-wide; values live per record.

## Field definitions

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/custom-fields/group/` | List field groups (folders for fields) |
| POST | `/api/internal/custom-fields/group/` | Create a group |
| PATCH / DELETE | `/api/internal/custom-fields/group/{id}/` | Manage a group |
| GET | `/api/internal/custom-fields/` | List field definitions |
| POST | `/api/internal/custom-fields/` | Create a field |
| GET / PATCH / DELETE | `/api/internal/custom-fields/{id}/` | Manage a field |
| GET / POST | `/api/internal/custom-fields/{field_id}/option/` | Options for select-type fields |
| GET / PATCH / DELETE | `/api/internal/custom-fields/{field_id}/option/{id}/` | Manage an option |

Fields have a type (text, number, date, select and so on); select fields carry their allowed values as options. Group related fields so the record page stays readable - a `Foreclosure` group holding `Auction Date`, `Opening Bid`, `Trustee`, for example.

## Per-record values

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/property/{property_uuid}/custom-field/` | Read a record's custom field values |
| PATCH | `/api/internal/property/{property_uuid}/custom-field/update-values/` | Write values on the record |

## The setup pattern

Build definitions once, idempotently, at integration setup:

```python
import os, requests
BASE = "https://apiv2.reisift.io"
H = {"Authorization": f"Api-Key {os.environ['DATASIFT_API_KEY']}",
     "Content-Type": "application/json"}

WANTED = ["Auction Date", "Opening Bid", "Trustee"]

existing = {f["title"]: f for f in
            requests.get(f"{BASE}/api/internal/custom-fields/", headers=H, timeout=30).json().get("data", [])}

for title in WANTED:
    if title not in existing:
        requests.post(f"{BASE}/api/internal/custom-fields/", headers=H,
                      json={"title": title}, timeout=30).raise_for_status()

# Verify: re-list and confirm every wanted field now exists
after = {f["title"] for f in
         requests.get(f"{BASE}/api/internal/custom-fields/", headers=H, timeout=30).json().get("data", [])}
assert all(t in after for t in WANTED)
```

> **Schema note:** field creation takes the field's title, type, and group placement; list responses may use `data` or `results` as the page key depending on resource. Read one existing field first and mirror its shape when creating programmatically.

## Practical rules

- **Cache definition ids.** Field and option ids are stable; resolve titles to ids once at startup, not per record.
- **Custom fields power presets.** Filter presets can filter on custom field values, which makes fields like `Auction Date` the backbone of time-driven funnels (for example, "auction inside 30 days" as a preset).
- **Write then read.** After `update-values`, read the record's values back; a wrong field id fails quietly from your code's perspective if you skip verification.
- **Do not delete fields casually.** Deleting a definition orphans every record's value for it. Rename instead, or migrate values first.
