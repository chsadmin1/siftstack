# Integrations

DataSift ships first-class integrations for dialers, texting platforms, email, calendars, and the DataFlik data service. Most dialer integrations expose a dedicated, simplified API surface so the external tool can read and write records with minimal payloads.

## Dialer and texting surfaces

Each connected platform gets its own namespace with property and owner endpoints shaped for that tool. These exist for the integration's use, but they are callable with your API key and can be convenient minimal surfaces:

| Namespace | Endpoints |
|---|---|
| `/calltools/` | `property/` list, create, retrieve |
| `/launch-control/` | `property/` list, create, retrieve |
| `/reirail/` | `property/` list, create, retrieve |
| `/smarter-contact/` | `property/`, `owner/`, `call/` |
| `/smrtphone/` | `property/`, `owner/`, `owner/{uuid}/message/`, `call/`, `sms/` with `update_status` |
| `/smrtdialer/` | `property/`, `owner/`, `owner/{uuid}/message/`, `call/`, `sms/` with `update_status` |
| `/xencall/` | `property/` list, create, retrieve |
| `/kixie/` | `property/`, `owner/`, `call/`, `sms/` with `update_status` |
| `/aircall/` | Call lifecycle webhooks: `calls/`, `answered/`, `ended/`, `voicemail-left/`, `agent-declined/`, `ringing-on-agent/` |
| `/twilio/sms/`, `/plivo/sms/` | Inbound SMS records CRUD |

Call and SMS events posted through these namespaces land on the matching owner and property records and increment the attempt counters, which keeps API-driven marketing cadence accurate no matter which dialer executes the touches.

There is also a top-level Open API surface (`/property/`, `/owner/`, `/phone/`, `/account/`) with add-tag, add-list, add-phone-tag, and add-phone-status actions - the original partner-facing surface. Prefer the `/api/internal/` paths for new work; the top-level surface remains for compatibility.

## Email integration

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/internal/email-integration/` | List / connect email accounts |
| GET / PATCH / DELETE | `/api/internal/email-integration/{id}/` | Manage a connection |
| GET | `/api/internal/email-integration/{email_integration_uuid}/read-email/` | Read messages |
| GET | `.../get-attachment/{message_id}/{attachment_id}/` | Fetch an attachment |
| POST | `.../send-email/` | Send an email through the connected account |

## Calendar integration

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/internal/calendar-integration/` | List / connect calendars |
| GET / PATCH / DELETE | `/api/internal/calendar-integration/{uuid}/` | Manage a connection |
| GET | `.../events/` | List events |
| POST | `.../events/` | Create an event (summary, description, ISO 8601 start and end, time_zone, attendees) |

## DataFlik

DataFlik is the connected data service for record deliveries and enrichment:

| Method | Path | Purpose |
|---|---|---|
| POST | `/dataflik/api-key/` | Connect a DataFlik key; `disconnect/` to remove |
| POST | `/dataflik/upload/` | Push an upload |
| GET | `/dataflik/upload/{file_id}/` | Read an upload's output |
| GET / POST | `/dataflik/property/`, `/dataflik/owner/` | DataFlik-scoped record surfaces with add-tag, add-list, add-phone-tag, add-phone-status actions |
| GET | `/dataflik/back-testing/{target_path}/` | Back-testing data passthrough |
| GET | `/api/internal/activity/dataflik/` | Deliveries in the activity feed, with `download-url` per delivery |

## Counties

`GET /api/internal/county/` and `GET /api/internal/county/{fips}/` resolve county records by FIPS code. When joining external county data, match on the 5-digit FIPS code, never on the county name; names repeat across states.

## Addons and account

`GET /api/internal/addon/` lists your plan addons and each addon's state; `preview-invoice/` and `subscribe/` manage them. `GET /api/internal/user/` and `GET /api/internal/account/{uuid}/` identify the key's user and account - useful as a startup sanity check in any integration.
