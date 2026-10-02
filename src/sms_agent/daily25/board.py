"""Pull the combined Acquisitions + Lead Flow candidate pool.

Reuses siftline_kpi.Api (JWT mint, board/column/card pagination, the timeline
null-gotcha) rather than writing a third copy of the same auth block -- two
already exist (siftline_kpi.py, datasift_api_upload.py) and a third is not an
improvement. One full property GET per card fills in investor_score,
equity_percent, owner.dnc/opt_out, and the owner's phone list (each phone
carrying its OWN tags/status), none of which pipeline_metrics() relies on
from the card's own embedded `prop` summary, so they are not assumed present.

Live-verified phone shape (2026-08-27):
    {"number": "9134756399", "type": "MOBILE", "tags": ["Dial First"],
     "status": "UNKNOWN", "is_connected": true}
`tags` is a list of TAG TITLES (strings), not uuids.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

# parents[2]: daily25 -> sms_agent -> src (or /app in the deployed container,
# where deploy/Dockerfile copies siftline_kpi.py alongside the sms_agent
# package for exactly this import).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import siftline_kpi  # noqa: E402

from . import config as d25config  # noqa: E402

log = logging.getLogger(__name__)

# Dispositions that mean "never text this number again" -- the same class of
# list seed.py already uses for the same reason. NO_ANSWER/UNKNOWN are call
# history, not a validity verdict, so they are deliberately not here.
SKIP_PHONE_STATUSES = {"DNC", "CORRECT_DNC", "WRONG_DNC", "WRONG", "DEAD"}


def _best_phone(phones: list[dict]) -> dict:
    """The phone this record will be texted on, or {} if none qualify.

    Mobile-first: a landline cannot receive SMS at all, which cost 6 of 35
    sends on 2026-08-31 (smrtPhone rejected each with "this number is
    landline type"). Mirrors the same "mobiles only" rule seed.py already
    applies for the same reason. UNKNOWN type is accepted as a fallback
    (better than nothing) but only after every MOBILE candidate is tried.
    """
    candidates = [
        p for p in phones
        if isinstance(p, dict) and p.get("number")
        and (p.get("status") or "").upper() not in SKIP_PHONE_STATUSES
    ]
    for wanted in ("MOBILE", "UNKNOWN"):
        for p in candidates:
            if (p.get("type") or "").upper() == wanted:
                return p
    return {}


def normalize_card(api: "siftline_kpi.Api", card: dict, board_name: str) -> Optional[dict]:
    """One SiftLine card -> one scoring candidate, or None if it wraps no property."""
    prop_summary = card.get("prop") or {}
    uuid = prop_summary.get("uuid")
    if not uuid:
        return None
    try:
        record = api.call(f"/api/internal/property/{uuid}/")
    except Exception as exc:  # noqa: BLE001 - one bad fetch must not drop the whole pull
        log.warning("could not fetch property %s: %s", uuid, exc)
        return None

    tl = card.get("_timeline") or {}
    cur = tl.get(card.get("_column_uuid")) or {}
    dwell_seconds = cur.get("time")
    dwell_days = dwell_seconds / 86400 if isinstance(dwell_seconds, (int, float)) else None

    addr = record.get("address") or {}
    owner = record.get("owner") or {}
    phones = owner.get("phones") or []
    best = _best_phone(phones)

    return {
        "property_uuid": uuid,
        "board_name": board_name,
        "column_title": card.get("_column_title", ""),
        "column_order": card.get("_column_order", 0),
        "dwell_days": dwell_days,
        "phone": best.get("number") or "",
        "phone_tags": set(best.get("tags") or []),
        "state": (addr.get("state") or "").strip().upper(),
        "street": addr.get("street") or "",
        "city": addr.get("city") or "",
        "status": (record.get("status") or "").strip(),
        "dnc": bool(owner.get("dnc")),
        "opt_out": bool(owner.get("opt_out")),
        "investor_score": record.get("investor_score"),
        "equity_percent": record.get("equity_percent"),
        "owner_first": (owner.get("first_name") or "").strip(),
        "owner_last": (owner.get("last_name") or "").strip(),
        "owner_company": (owner.get("company") or "").strip(),
    }


def pull_candidates(api: "siftline_kpi.Api") -> list[dict]:
    """Combined candidate pool from both configured boards, each candidate
    tagged with its own board's column-count so urgency normalizes per board
    rather than assuming both boards have the same number of columns."""
    out: list[dict] = []
    for board_name, board_uuid in d25config.BOARDS:
        log.info("pulling board %s (%s)...", board_name, board_uuid)
        columns, cards = siftline_kpi.load_board(api, board_uuid)
        max_order = max((c.get("order", 0) for c in columns), default=0) or 1
        board_candidates = []
        for card in cards:
            cand = normalize_card(api, card, board_name)
            if cand is None:
                continue
            cand["_max_column_order"] = max_order
            board_candidates.append(cand)
        log.info("  %s: %d cards -> %d candidates", board_name, len(cards), len(board_candidates))
        out.extend(board_candidates)
    return out
