"""Persisted state for the two P1 List Manager jobs. Plain JSON files (the
volume of state here -- county snapshots, a handful of pending Slack asks --
never justifies a database). Honors SIFTSTACK_STATE_DIR / SIFTSTACK_OUTPUT_DIR
the same way src/config.py and src/dispo_flip_buyers.py already do, so this
runs unchanged on a workstation checkout or a Fly volume at /data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

STATE_DIR = Path(os.getenv("SIFTSTACK_STATE_DIR", ".")) / "p1_manager"
OUTPUT_DIR = Path(os.getenv("SIFTSTACK_OUTPUT_DIR", "output")) / "p1_manager"

WEEKLY_STATE_FILE = STATE_DIR / "weekly_state.json"
MONTHLY_SNAPSHOTS_FILE = STATE_DIR / "monthly_snapshots.json"
PENDING_ASKS_FILE = STATE_DIR / "pending_slack_asks.json"


def _load(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return default


def _save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---- weekly job: a single "since" cursor shared across all P1 presets ----

def load_weekly_state() -> dict:
    return _load(WEEKLY_STATE_FILE, {})


def save_weekly_state(state: dict) -> None:
    _save(WEEKLY_STATE_FILE, state)


# ---- monthly job: one snapshot per county, keyed "STATE-County" ----

def load_monthly_snapshots() -> dict:
    return _load(MONTHLY_SNAPSHOTS_FILE, {})


def save_monthly_snapshot(county_key: str, snapshot: dict) -> None:
    all_snaps = load_monthly_snapshots()
    all_snaps.setdefault(county_key, []).append(snapshot)
    _save(MONTHLY_SNAPSHOTS_FILE, all_snaps)


def latest_monthly_snapshot(county_key: str) -> dict | None:
    snaps = load_monthly_snapshots().get(county_key) or []
    return snaps[-1] if snaps else None


def preset_count_history() -> list[dict]:
    """[{month, county, total_presets}, ...] across every stored snapshot --
    the running total Dan asked the monthly report to track."""
    out = []
    for county_key, snaps in load_monthly_snapshots().items():
        for s in snaps:
            out.append({"county": county_key, "month": s.get("month"),
                        "total_presets": s.get("total_presets")})
    return out


# ---- pending Slack asks: thread_ts -> the batch waiting on a reply ----

def load_pending_asks() -> dict:
    return _load(PENDING_ASKS_FILE, {})


def save_pending_ask(thread_ts: str, payload: dict) -> None:
    asks = load_pending_asks()
    asks[thread_ts] = payload
    _save(PENDING_ASKS_FILE, asks)


def pop_pending_ask(thread_ts: str) -> dict | None:
    asks = load_pending_asks()
    payload = asks.pop(thread_ts, None)
    if payload is not None:
        _save(PENDING_ASKS_FILE, asks)
    return payload
