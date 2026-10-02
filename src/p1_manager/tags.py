"""Dan's P1 tagging standard (call notes, 2026-09-22), applied on every pull.

Three tags per record, per pull, so performance traces back to source:
  1. `P1`                       -- bare safety-net tag; add if somehow missing
  2. `Last Pulled MM-DD-YYYY`   -- date of THIS pull run
  3. `P1 | MMYY | <List Name>`  -- composite traceability tag. MMYY has no
     separator (October 2026 = "1026"). <List Name> is the playbook combo
     label exactly as supplied (e.g. "Free and Clear + Vacant") -- NEVER a
     consolidated/boolean-simplified label, even when two combos look
     logically redundant. Preserve them as separate presets/pulls and let
     de-dup remove duplicate records afterward; don't merge on Boolean logic.

All three are written with DataSiftClient.bulk_add_tags(), the safe nested-
query shape -- see datasift_client.py's module docstring for why that matters.
"""
from __future__ import annotations

from datetime import date

from datasift_client import DataSiftClient

BARE_TAG = "P1"


def last_pulled_tag(pull_date: date | None = None) -> str:
    d = pull_date or date.today()
    return f"Last Pulled {d.strftime('%m-%d-%Y')}"


def composite_tag(list_name: str, pull_date: date | None = None) -> str:
    d = pull_date or date.today()
    mmyy = d.strftime("%m%y")
    return f"P1 | {mmyy} | {list_name}"


def tag_pull(client: DataSiftClient, property_uuids: list[str], list_name: str,
             *, pull_date: date | None = None) -> dict[str, int]:
    """Apply all three tags to exactly `property_uuids`. Returns counts per tag
    (the API's own reported `count`, not necessarily == len(property_uuids) if
    some records already carried a tag being re-added)."""
    if not property_uuids:
        return {"P1": 0, "last_pulled": 0, "composite": 0}
    tags = [BARE_TAG, last_pulled_tag(pull_date), composite_tag(list_name, pull_date)]
    done = client.bulk_add_tags(property_uuids, tags)
    return {"P1": done, "last_pulled": done, "composite": done}


def backfill_missing_p1(client: DataSiftClient, property_uuids: list[str]) -> int:
    """Records found on a P1 pull that somehow lack the bare P1 tag get it
    added -- Dan's explicit safety net: 'should usually already be present...
    if any records... do not have a P1 tag, please add.'"""
    if not property_uuids:
        return 0
    return client.bulk_add_tags(property_uuids, [BARE_TAG])
