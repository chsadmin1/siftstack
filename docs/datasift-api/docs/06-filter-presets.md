# Filter Presets and Scheduled Exports

Filter presets are saved record filters: the building blocks of a repeatable marketing system. A preset captures a query over your records (lists, tags, statuses, counters, custom fields); folders organize presets into a funnel; scheduled exports turn a preset into a recurring data feed without any polling code.

## Preset CRUD

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/filter-preset/` | List presets |
| POST | `/api/internal/filter-preset/` | Create a preset |
| GET | `/api/internal/filter-preset/{uuid}/` | Detail (includes the stored filter definition) |
| PUT / PATCH | `/api/internal/filter-preset/{uuid}/` | Update |
| DELETE | `/api/internal/filter-preset/{uuid}/` | Delete |
| POST | `/api/internal/filter-preset/compile/` | Compile a filter definition (validate and resolve it server side) |

Preset folders follow the standard folder pattern at `/api/internal/filter-preset-folder/`, with nested preset management at `/api/internal/filter-preset-folder/{folder_uuid}/filter-preset/...`.

## The compile endpoint

`POST /api/internal/filter-preset/compile/` takes a filter definition and returns the compiled form the platform will execute. Use it as a pre-flight check when building presets programmatically: compile first, inspect what resolved, then create the preset. This catches misspelled tag names and malformed clauses before they become a silently empty preset.

## Building a preset programmatically

The reliable pattern for constructing filter definitions in code:

1. Build one preset by hand in the app the way you want it.
2. `GET` that preset by uuid and study the stored filter definition.
3. Template it: swap the tag and list uuids for your targets.
4. `POST /api/internal/filter-preset/compile/` to validate.
5. Create the preset, then `GET` it back and diff against your intent.

This read-one-then-template approach survives platform filter-language evolution far better than hand-writing filter JSON from scratch.

## Scheduled exports

Each preset can carry one scheduled export: the platform runs the preset on a schedule and produces an export artifact.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/filter-preset/{filter_preset_uuid}/scheduled-export/` | Read the schedule |
| POST | `/api/internal/filter-preset/{filter_preset_uuid}/scheduled-export/` | Create it |
| PATCH | `/api/internal/filter-preset/{filter_preset_uuid}/scheduled-export/` | Change it |
| DELETE | `/api/internal/filter-preset/{filter_preset_uuid}/scheduled-export/` | Remove it |

Scheduled exports replace "cron job that hits the list endpoint every morning" for most feed use cases: define the preset, schedule the export, and pick up the artifact. Verify the first artifact manually before trusting the schedule (row count and a spot check of columns), and re-verify after any preset edit.

## Funnel design with preset folders

A production marketing funnel is a folder of presets in stage order, where each preset both selects the current stage and excludes everything later:

```
Folder: Niche Sequential - Foreclosure
  01 Ready to Skip Trace     (on list, not tagged 01+)
  02 Ready to Call           (tagged 01, has phones, not tagged 02+)
  03 Ready for Mail          (tagged 02, call attempts >= N, not tagged 03+)
  04 Deep Prospect           (exhausted 01-03, no contact)
  Suppression - Done or Dead (terminal tags; excluded by every stage above)
```

Records flow down as tags accumulate: work stage 02, tag records `02`, and they drop from preset 02 into preset 03's scope automatically. The suppression preset is the mirror image that every stage excludes. Verification for a funnel build: every stage preset compiles, the stage counts sum to no more than the source list count, and no record appears in two stages (spot-check a few uuids).
