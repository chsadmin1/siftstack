"""Persist/read "today's 35" so the 09:00 send and 18:00 voicemail follow-up
act on the EXACT same records the 08:00 select run picked and tasked,
without a second full board pull + re-rank (which could pick differently if
anything moved columns in the meantime).

Stored via sms_agent.store's existing meta table (store.set_meta/get_meta) --
no new table needed, this is the same mechanism scheduler.py's heartbeat and
mark_notified already build on.
"""
from __future__ import annotations

import json

from .. import store

# Only what send_run/voicemail_run actually need -- deliberately not the full
# scoring candidate dict, so a schema change in board.py/scoring.py can't
# silently break a batch already persisted for today.
_FIELDS = (
    "property_uuid", "phone", "street", "city", "state",
    "owner_first", "owner_last", "owner_company", "board_name", "_score",
)


def _key(date_iso: str) -> str:
    return f"daily25_batch_{date_iso}"


def save(date_iso: str, candidates: list[dict]) -> None:
    rows = [{k: c.get(k) for k in _FIELDS} for c in candidates]
    store.set_meta(_key(date_iso), json.dumps(rows))


def load(date_iso: str) -> list[dict]:
    raw = store.get_meta(_key(date_iso))
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []
