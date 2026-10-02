# DataSift Developer Documentation (Extended)

Extended developer documentation for the **DataSift / REISift** platform APIs. This builds on the official reference at [developers.datasift.ai](https://developers.datasift.ai/) with the request patterns, workflows, and use cases the reference does not yet cover.

Every request authenticates with a **REISift Open API key**. That is the only supported authentication method for API access.

```
Authorization: Api-Key YOUR_OPEN_API_KEY
```

## The two API surfaces

| API | Base URL | What lives here | Official reference |
|---|---|---|---|
| DataSift API Core | `https://apiv2.reisift.io` | Your account's records: properties, owners, phones, tags, lists, filter presets, tasks, SiftLine boards, custom fields, activity | [developers.datasift.ai/datasift](https://developers.datasift.ai/datasift/) |
| SiftMap API | `https://map.reisift.io` | Nationwide property mapping, search, comparables, and map filters with auto-add | [developers.datasift.ai/sift-map](https://developers.datasift.ai/sift-map/) |

The same API key works on both.

## Contents

| Doc | What it covers |
|---|---|
| [docs/00-quickstart.md](docs/00-quickstart.md) | Get a key, make your first calls in curl and Python, verify your setup |
| [docs/01-authentication.md](docs/01-authentication.md) | The Api-Key scheme, plan requirements, permissions, key rotation |
| [docs/02-conventions.md](docs/02-conventions.md) | Pagination, ordering, errors, write verification, platform quirks |
| [docs/03-properties.md](docs/03-properties.md) | Property records: list, create, bulk import, dedupe, statuses, notes, assignment, skip trace, attempt counters, documents and images |
| [docs/04-owners-and-phones.md](docs/04-owners-and-phones.md) | Owners, phone upserts, phone tags, SMS, message board, offers |
| [docs/05-tags-lists-folders.md](docs/05-tags-lists-folders.md) | Tags, lists, their folders, counts, and how to organize a marketing funnel with them |
| [docs/06-filter-presets.md](docs/06-filter-presets.md) | Filter presets, preset folders, compile, and scheduled exports |
| [docs/07-tasks-and-siftline.md](docs/07-tasks-and-siftline.md) | Tasks, task groups and presets, recurrence, SiftLine boards, columns, cards, deals |
| [docs/08-custom-fields.md](docs/08-custom-fields.md) | Custom field groups, fields, options, and per-property values |
| [docs/09-activity-and-reporting.md](docs/09-activity-and-reporting.md) | Activity feed, exports, skip trace stats, dashboards, KPI patterns |
| [docs/10-integrations.md](docs/10-integrations.md) | Dialer and SMS integrations, email, calendar, DataFlik, webhooks |
| [docs/11-siftmap-api.md](docs/11-siftmap-api.md) | SiftMap: map filters with auto-add snapshots, add-properties, search, property detail |
| [docs/12-endpoint-index.md](docs/12-endpoint-index.md) | The complete generated index: all 574 core operations grouped by resource |
| [docs/13-use-case-playbooks.md](docs/13-use-case-playbooks.md) | End-to-end recipes: record dossier, marketing preset build, suppression, dial tiering, bulk import with verification, KPI reporting, SiftLine automation |
| [SETUP_CHECKLIST.md](SETUP_CHECKLIST.md) | One-page setup checklist |

## The one operating rule

**Verify the data, never the exit code.** After every write, read the resource back (or read its count) and confirm the change actually landed. Batch and background operations in particular can accept a request and complete differently than you expect. Every playbook in this documentation ends with a verification read.

## Status of this documentation

The official reference is generated from the API schema and is authoritative for endpoint paths and methods. Some generated request-body schemas are generic placeholders; where this documentation and the generated schema differ, this documentation reflects observed working behavior, and each such case is flagged inline with "schema note". Report discrepancies to the DataSift team.
