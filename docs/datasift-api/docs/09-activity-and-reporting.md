# Activity, Exports, and Reporting

The activity system is the account's event log; the export endpoints turn queries into files; the dashboard endpoints expose the app's reporting; and a handful of stats endpoints cover skip tracing. Together they are the raw material for KPI reporting.

## Activity feed

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/activity/` | The account activity feed (paginated) |
| GET | `/api/internal/activity/{uuid}/export/` | Export an activity artifact |
| GET | `/api/internal/activity/{uuid}/export-url/` | Get a download URL for an export |
| GET | `/api/internal/activity/dataflik/` | DataFlik-sourced activities |
| GET | `/api/internal/activity/dataflik/{uuid}/download-url/` | Download a DataFlik delivery |

Activities cover the operational events on the account - imports, exports, skip trace runs, data deliveries. Poll the feed with a `since`-style cursor from your last seen activity and you have a lightweight change stream for the account.

## Skip trace stats

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/activity/skiptrace/stats/` | Aggregate skip trace statistics |
| GET | `/api/internal/activity/skiptrace/chart/` | Time-series data behind the app's skip trace chart |

Use these to monitor a bulk skip trace submission: submit via the property skip-trace endpoint, then watch stats move rather than polling every record.

## Dashboards

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/dashboard/` | List available dashboards |
| GET | `/api/internal/dashboard/{slug}/` | A dashboard's definition and data |
| GET | `/api/internal/dashboard-report/{uuid}/` | A specific dashboard report |

The dashboards the app renders are readable through the API, which means any metric you can see in the app you can pull into your own reporting.

## Per-record logs

`GET /api/internal/property/{uuid}/logs/` and `GET /api/internal/owner/{uuid}/logs/` return the change history of a single record - who changed what, when. This is the ground truth for per-record QA.

## Building a KPI report

The pattern for a daily KPI job, using only counting reads:

1. **Define each KPI as a query you can count.** Stage tags, statuses, and phone tags all expose `properties-count`; the property list returns `count` for any filtered query; dashboards expose the app's own metrics.
2. **Snapshot on a schedule.** Once a day, read every count and store the snapshot with a date stamp on your side.
3. **Report deltas, not levels.** New-records-today = today's count minus yesterday's. Movement between stages = per-stage deltas.
4. **Verify the pipeline itself.** Alert when a count that should always grow goes flat (broken import) or a suppression count stops moving (broken suppression job).

Scheduled exports on filter presets (`06-filter-presets.md`) complement this: counts for the topline numbers, exports when you need the row-level data behind them.
