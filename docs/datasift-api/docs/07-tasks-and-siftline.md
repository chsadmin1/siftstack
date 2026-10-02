# Tasks, Task Presets, and SiftLine

Tasks are the to-do layer on records; task groups and presets template them; recurrence repeats them; SiftLine is the kanban pipeline (boards, columns, cards) for working deals. Deals are the transaction objects that ride along with pipeline work.

## Tasks

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/task/` | List tasks |
| POST | `/api/internal/task/` | Create a task |
| GET | `/api/internal/task/{uuid}/` | Detail |
| PUT / PATCH | `/api/internal/task/{uuid}/` | Update |
| DELETE | `/api/internal/task/{uuid}/` | Delete |
| GET | `/api/internal/task/{uuid}/property/` | The property a task belongs to |
| POST | `/api/internal/task/{uuid}/complete/` | Complete |
| POST | `/api/internal/task/{uuid}/uncomplete/` | Reopen |
| POST | `/api/internal/task/bulk-complete/` | Complete many at once |
| POST | `/api/internal/task/{uuid}/set-outcome/` | Record an appointment outcome (status, result, notes) |
| POST | `/api/internal/task/create-by-preset/` | Instantiate a task from a preset |
| POST | `/api/internal/task/create-by-group/` | Instantiate every preset in a group |
| POST | `/api/internal/task/{uuid}/enable-recurrence/`, `.../disable-recurrence/` | Toggle recurrence on a task |

## Task groups and presets

Task presets are task templates; groups bundle presets into a standard operating procedure. `create-by-group` on a record stamps out the whole SOP in one call - the API equivalent of "apply my follow-up sequence to this lead".

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/internal/task-group/` | List / create groups |
| GET / POST | `/api/internal/task-group/{task_group_uuid}/task-preset/` | Presets within a group |
| GET / POST | `/api/internal/task-preset/` | All presets, group-independent |
| GET | `/api/internal/task-recurrence/` | List recurrences; enable and disable per recurrence uuid |

## SiftLine - boards, columns, cards

SiftLine models your deal pipeline as boards containing columns containing cards, where a card wraps a property record.

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/internal/siftline/board/` | List / create boards |
| GET / PUT / PATCH / DELETE | `/api/internal/siftline/board/{uuid}/` | Board management (board detail includes its columns) |
| GET / POST | `/api/internal/siftline/board/column/{column_uuid}/card/` | List / create cards in a column |
| GET / PUT / PATCH / DELETE | `/api/internal/siftline/board/column/{column_uuid}/card/{uuid}/` | Card management; moving a card between columns is a card update |
| GET | `/api/internal/siftline/board/column/{column_uuid}/card/{uuid}/timeline/` | Card history |
| GET | `.../card/{uuid}/next/`, `.../prev/` | Walk cards in column order |
| POST | `/api/internal/siftline/board/column/card-bulk/` | Bulk card operations |

The SiftLine namespace also mirrors the full task, task-group, and task-preset API under `/api/internal/siftline/...` for pipeline-scoped task work.

### The QA automation pattern

The card timeline endpoint makes pipeline hygiene measurable. For each active card: read its timeline, compute time-in-column, check that the tasks its column requires exist and are complete, and flag cards that moved columns with incomplete tasks. Run it on a schedule and you have an automated pipeline audit. The full recipe is in `13-use-case-playbooks.md`.

## Deals

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/internal/deal/` | List / create deals |
| GET / PUT / PATCH / DELETE | `/api/internal/deal/{uuid}/` | Deal management |
| GET | `/api/internal/property/{uuid}/deal/` | The deal attached to a property |

## Calendar-visible work

Tasks with due dates surface in the app calendar. If you connect a calendar integration (`10-integrations.md`), appointment-type tasks can flow to an external calendar; `set-outcome` is how completed appointments feed reporting.
