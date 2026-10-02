# Tags, Lists, and Folders

Tags and lists are the two labeling systems on property records. Lists represent where a record CAME FROM (the source list you imported); tags represent what you KNOW or have DONE (motivation markers, workflow states, suppression flags). Both organize into folders. Everything here is account-wide configuration; applying them to records happens through the property actions in `03-properties.md`.

## Tags

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/tag/` | List tags |
| POST | `/api/internal/tag/` | Create a tag |
| GET | `/api/internal/tag/{uuid}/` | Tag detail |
| PATCH | `/api/internal/tag/{uuid}/` | Rename or edit |
| DELETE | `/api/internal/tag/{uuid}/` | Delete (confirm before destructive ops) |
| GET | `/api/internal/tag/{uuid}/properties-count/` | Records carrying this tag |

Tag folders: same CRUD pattern at `/api/internal/tag-folder/`, plus nested tag management at `/api/internal/tag-folder/{folder_uuid}/tag/...` so you can create a tag directly inside a folder.

## Lists

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/list/` | List lists |
| POST | `/api/internal/list/` | Create a list |
| GET | `/api/internal/list/{uuid}/` | Detail |
| PATCH | `/api/internal/list/{uuid}/` | Edit |
| DELETE | `/api/internal/list/{uuid}/` | Delete |
| GET | `/api/internal/list/{uuid}/properties-count/` | Records on this list |

List folders mirror tag folders at `/api/internal/list-folder/` with nested list management.

## The counts are your instrumentation

`properties-count` on tags, lists, and phone tags is the cheapest verification and reporting primitive in the platform. Use it to:

- Verify bulk operations (tag 2,000 records, then confirm the count moved by 2,000)
- Monitor funnel stages (each stage tag's count is a live pipeline metric)
- Detect drift (a suppression tag whose count stops growing means the suppression job broke)

## Naming discipline

A naming convention keeps an account navigable once tags reach the hundreds:

- Prefix by system: `FTM - `, `Bulk - `, `Phone - `, `Supp - ` and group each prefix into its folder.
- Use plain ASCII hyphens in names; the platform strips em dashes from titles.
- Encode stage order in names where sequencing matters (`01 Skip Trace`, `02 Calling`, `03 Mail`), so alphabetical order matches funnel order.
- One meaning per tag. When a tag means two things, split it; filters and counts stay trustworthy.

## Worked example - create a folder with staged tags

```python
import os, requests
BASE = "https://apiv2.reisift.io"
H = {"Authorization": f"Api-Key {os.environ['DATASIFT_API_KEY']}",
     "Content-Type": "application/json"}

folder = requests.post(f"{BASE}/api/internal/tag-folder/", headers=H,
                       json={"title": "Marketing Stages"}, timeout=30).json()

for name in ["01 Skip Traced", "02 Calling", "03 Mail Sent", "04 Deep Prospect", "Done - Dead"]:
    requests.post(f"{BASE}/api/internal/tag-folder/{folder['uuid']}/tag/",
                  headers=H, json={"title": name}, timeout=30).raise_for_status()

# Verify: read the folder's tags back and check all five exist
tags = requests.get(f"{BASE}/api/internal/tag-folder/{folder['uuid']}/tag/",
                    headers=H, timeout=30).json()
```

> **Schema note:** tag and list objects use a short payload (title plus optional folder placement). If a create call 400s, the validation message names the expected field; adjust and re-send.

## Global statuses

Statuses are the one-per-record pipeline state (New Lead, Not Interested, Return Mail, and so on), managed at `GET /api/internal/global-status/` with `PUT`/`PATCH` per status. Statuses are account-wide and finite; use tags for anything that can co-exist, and status for the single current state.
