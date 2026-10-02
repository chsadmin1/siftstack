"""Task creation for Wendy's daily call list.

POST /api/internal/task/ exists (docs/datasift-api/docs/07-tasks-and-siftline.md)
but no field names were confirmed anywhere in this repo or the docs. Verified
live 2026-08-27 by reading GET /api/internal/task/ (an account already
holding 4,116 tasks): a real task's shape is

    {"title": ..., "due_date": "2025-11-20T20:00:00Z",
     "assigned_to_user": {"uuid": ..., "email": ..., "first_name": ..., "last_name": ...},
     "assigned_to_property": "<property uuid>", ...}

i.e. `assigned_to_property` (flat uuid, matching its read shape exactly) and
`assigned_to_user` (nested object on read; create_task sends a flat uuid,
matching the account's usual nested-on-read/flat-id-on-write convention) --
NOT the generic `property`/`assigned_to` guessed before this was checked.
`due_date` is a full ISO datetime, not a bare date. This was confirmed with
one real created task read back before ever running across a batch (see the
"verify one before trusting it" discipline documented for every other
undocumented endpoint in this codebase, e.g. datasift_api_upload.py).

GET /api/internal/user/ also turned out to be single-user ("retrieveUser":
the DATASIFT_EMAIL account owner), not a team-member list -- there is no
documented way to resolve an arbitrary teammate's uuid by email, the same gap
config/sms_senders.json's own note describes for the SMS side. Wendy's uuid
is a verified constant in config.py; scan_boards_for_assignee() below exists
only to re-confirm that constant in --doctor, not as the runtime path (a
second board scan per run would double the API cost for no benefit).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def scan_boards_for_assignee(api, boards: list[tuple[str, str]], email: str) -> str:
    """Doctor-only check: does any card on these boards show `email` as the
    assignee, and does its uuid match config.WENDY_UUID? Costs one pass over
    every card on both boards, which is why the real run path never calls this."""
    import siftline_kpi  # local import: only needed for this doctor-time scan

    email = email.strip().lower()
    for _name, board_uuid in boards:
        columns, cards = siftline_kpi.load_board(api, board_uuid)
        for card in cards:
            at = (card.get("prop") or {}).get("assigned_to")
            if isinstance(at, dict) and (at.get("email") or "").strip().lower() == email:
                return at.get("uuid") or ""
    return ""


def sample_task_shape(api) -> dict:
    """A couple of real tasks, if any exist, to mirror their field names
    before ever POSTing a cold guess."""
    return api.call("/api/internal/task/", params={"limit": 3, "offset": 0})


def create_task(api, property_uuid: str, assignee_uuid: str, due_date_iso: str, title: str) -> dict:
    """POST /api/internal/task/. See the module docstring for how this shape
    was verified."""
    body = {
        "title": title,
        "assigned_to_property": property_uuid,
        "assigned_to_user": assignee_uuid,
        "due_date": due_date_iso,
    }
    return api.call("/api/internal/task/", method="POST", body=body)


def read_task(api, task_uuid: str) -> dict:
    return api.call(f"/api/internal/task/{task_uuid}/")


def delete_task(api, task_uuid: str) -> dict:
    return api.call(f"/api/internal/task/{task_uuid}/", method="DELETE")
