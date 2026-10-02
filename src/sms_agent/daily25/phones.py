"""Trestle dial-tier scoring, applied narrowly: only a candidate's own phone,
only when it carries none of the five tier tags yet. No bulk backfill of the
wider ~195-phone pool, and never re-score an already-tagged number.

A minimal, self-contained Trestle client rather than importing
src/phone_validator.py: that module does `import config` at load time (the
large, TN-scraper-oriented top-level src/config.py, with its own directory
creation and unrelated env expectations), which is unnecessary baggage to
carry into the sms_agent container for the ~10 lines actually needed here.
The score-to-tier mapping mirrors phone_validator.DEFAULT_TIERS exactly.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from . import config as d25config

log = logging.getLogger(__name__)

TRESTLE_ENDPOINT = "https://api.trestleiq.com/3.0/phone_intel"
_TIERS = {
    "Dial First": (81, 100),
    "Dial Second": (61, 80),
    "Dial Third": (41, 60),
    "Dial Fourth": (21, 40),
    "Drop": (0, 20),
}


def _assign_tier(score: Optional[int]) -> str:
    if score is None:
        return "Unknown"
    for name, (lo, hi) in _TIERS.items():
        if lo <= score <= hi:
            return name
    return "Unknown"


def _call_trestle(phone: str) -> Optional[int]:
    if not d25config.TRESTLE_API_KEY:
        return None
    try:
        resp = requests.get(
            TRESTLE_ENDPOINT,
            params={"phone": phone},
            headers={"x-api-key": d25config.TRESTLE_API_KEY, "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning("Trestle HTTP %s for %s", resp.status_code, phone)
            return None
        return resp.json().get("activity_score")
    except requests.RequestException as exc:
        log.warning("Trestle call failed for %s: %s", phone, exc)
        return None


def ensure_scored(api, cand: dict) -> Optional[str]:
    """Trestle-score cand's phone iff it carries no dial-tier tag yet.

    Returns the tier written, or None if already tagged, no phone, no
    Trestle key configured, or the call/write failed -- never raises, since
    this is enrichment and must never block task creation or texting.
    """
    phone = cand.get("phone")
    if not phone:
        return None
    if cand.get("phone_tags") & d25config.DIAL_TIER_NAMES:
        return None  # already scored -- never re-score

    score = _call_trestle(phone)
    tier = _assign_tier(score)
    if tier == "Unknown":
        return None

    try:
        api.call(
            f"/api/internal/property/{cand['property_uuid']}/add-phone-tag/",
            method="POST",
            body={"phone": phone, "tag": tier},
        )
    except Exception as exc:  # noqa: BLE001 - enrichment must never block the run
        log.warning("could not write phone tag %s for %s: %s", tier, phone, exc)
        return None
    cand["phone_tags"].add(tier)
    return tier
