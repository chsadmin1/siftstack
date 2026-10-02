"""Exclusion filters + weighted urgency/quality scoring for the daily 35.

Named constants, not magic numbers, mirroring the transparent
weighted-composite style of market_analyzer.py / obituary_opportunity.py.
"""
from __future__ import annotations

import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo

from .. import store
from . import config


def recently_texted_phones() -> set[str]:
    """Phones sent a daily25 pre-call text within COOLDOWN_DAYS.

    Shared by select_run (holds the draft back) and send_run (re-checked an
    hour later against fresh message history, since a record could have been
    texted by something else in the interim).
    """
    cutoff = (
        datetime.datetime.now(ZoneInfo(config.BUSINESS_TIMEZONE)) - timedelta(days=config.COOLDOWN_DAYS)
    ).astimezone(datetime.timezone.utc).isoformat()
    rows = store._conn().execute(
        "SELECT DISTINCT phone FROM messages WHERE intent=? AND created_at>=?",
        (config.PRECALL_INTENT, cutoff),
    )
    return {r["phone"] for r in rows}


def excluded(cand: dict, today_tag: str, recently_texted: set[str]) -> str:
    """Reason this candidate is excluded from PICKING entirely, or "" if it
    survives to be scored. The 3-day cooldown does NOT live here -- it only
    blocks the text, not the pick/task, so it's checked separately in
    select_run.py against the already-picked top 35 (see that module)."""
    if not cand.get("phone"):
        return "no usable phone"
    if cand.get("dnc") or cand.get("opt_out"):
        return "dnc / opted out"
    status = (cand.get("status") or "").strip().lower()
    if status in config.DEAD_STATUSES:
        return f"dead status ({status})"
    return ""


def cooldown_blocked(cand: dict, recently_texted: set[str]) -> bool:
    """True if this phone already got a pre-call text within COOLDOWN_DAYS.

    Blocks the TEXT only -- a cooled-down record is still picked, scored and
    tasked, per the explicit rule: the call task always goes out, only the
    text is held back.
    """
    return cand.get("phone") in recently_texted


def _urgency(cand: dict) -> float:
    """Column position (later = more urgent) blended with staleness.

    Falls back to position alone when the timeline endpoint hasn't computed a
    dwell value yet (None) -- the documented gotcha (siftline_kpi.py) is that
    a null there means "not yet recomputed," not zero, so it must never be
    silently treated as zero dwell (which would score as freshly-arrived).
    """
    max_order = max(cand.get("_max_column_order") or 1, 1)
    position = min(cand.get("column_order", 0) / max_order, 1.0)
    dwell_days = cand.get("dwell_days")
    if dwell_days is None:
        return position
    staleness = min(dwell_days / config.STALE_DAYS, 1.0) if config.STALE_DAYS else 0.0
    return (position + staleness) / 2


def _quality(cand: dict) -> float:
    """Average of whatever quality signals are actually present (0-1 each).

    A record with neither signal scores 0 on quality rather than being
    excluded from ranking outright -- pure urgency can still surface it, but
    it never wins a close call against a record with real numbers behind it.
    """
    parts = []
    investor = cand.get("investor_score")
    if isinstance(investor, (int, float)):
        parts.append(min(max(investor, 0), 100) / 100)
    equity = cand.get("equity_percent")
    if isinstance(equity, (int, float)):
        parts.append(min(max(equity, 0), 100) / 100)
    return sum(parts) / len(parts) if parts else 0.0


def score(cand: dict) -> float:
    return config.URGENCY_WEIGHT * _urgency(cand) + config.QUALITY_WEIGHT * _quality(cand)


def select_top(
    candidates: list[dict], today_tag: str, recently_texted: set[str], n: int
) -> tuple[list[dict], list[dict]]:
    """Returns (top-n survivors sorted best first, everything excluded with a reason).

    `recently_texted` is accepted here for a future exclusion tune but is
    currently applied post-selection in select_run.py (see cooldown_blocked),
    not as a pick-time filter -- the cooldown blocks the TEXT, not the pick.
    """
    survivors, dropped = [], []
    for cand in candidates:
        reason = excluded(cand, today_tag, recently_texted)
        if reason:
            cand["_exclude_reason"] = reason
            dropped.append(cand)
            continue
        cand["_score"] = round(score(cand), 4)
        survivors.append(cand)
    survivors.sort(key=lambda c: -c["_score"])
    return survivors[:n], dropped
