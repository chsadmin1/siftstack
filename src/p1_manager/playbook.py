"""County List Playbook reader for P1 List Manager.

Deliberately uses `capture-data` (the marginal doors-per-deal ladder), the
SAME source .claude/skills/county-list-preset-builder already uses and the
one that actually renders on the public playbook page -- NOT this repo's own
`county-data` (standalone-scored), which CLAUDE.md's own "County List
Playbook shards" section flags as currently reading ~20% high on doors/deal
from a known stale June-2026 zero-deals bug. Confirmed with Dan 2026-09-22.

Fetch is unauthenticated and read-only:
    https://learn.datasift.ai/capture-data/<2-digit-state-fips>.json
keyed by 5-digit county FIPS. `variants.lists` is the "All lists" view (what
this module always uses -- the P1-shortcut convention never asks about the
AI-score toggle). Rows are already in correct rung order; never re-sort.

The field-mapping table below is copied from
.claude/skills/county-list-preset-builder/references/siftmap-api.md, which
was reverse-engineered live against this account. Treat it the same way that
skill does: a well-tested starting point, not a permanent guarantee, and
never invent a field name for anything not in this table -- verify live via
count-delta (see siftmap-api.md) before trusting a new one.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

AI_SCORE_RE = re.compile(r"^AI Score (\d+)\s*-\s*(\d+)$", re.I)

CAPTURE_BASE = "https://learn.datasift.ai/capture-data"
# The site rejects the default urllib User-Agent outright (connection reset
# mid-handshake) -- verified live 2026-09-22. A plain browser UA clears it.
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
    "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
    "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
    "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56",
}

# Simple boolean filters, keyed by the playbook's plain-English combo token.
SIMPLE_FIELD_MAP = {
    "Absentee": {"extra_owner_absentee": True},
    "Vacant": {"extra_vacant": True},
    "Free & Clear": {"preset_free_clear": True},
    "Free and Clear": {"preset_free_clear": True},
    "High Equity": {"preset_high_equity": True},
    "Negative Equity": {"preset_negative_equity": True},
    "Low Equity": {"preset_low_equity": True},
}
# Out-of-State needs two fields together, confirmed from 3 independent live
# presets, required even when the label only says "Out-of-State".
OUT_OF_STATE = {"extra_owner_absentee": True, "extra_absentee_in_state": False}

# Distressor-checklist codes (Homeowner & Property Distressors -> Custom
# Combination in the SiftMap UI). Label -> code.
DISTRESSOR_CODES = {
    "Notice of Default": "is_notice_of_default",
    "Bad Credit": "is_bad_credit",
    "Low Income": "is_low_income",
    "Probate": "is_probate_properties",
    # Confirmed live 2026-09-23 by reading it straight off 6 existing P1
    # presets that already use it in a combo (Wyandotte/Cass/Platte, MO/KS),
    # e.g. filter_data.filters == {"distressors_include": "is_tired_landlord", ...}.
    "Tired Landlord": "is_tired_landlord",
    "Judgment Lien": "is_judgment_liens",
    "Tax Delinquent": "is_recent_deliquent",
    "Estate Sale": "is_estate_sales",
    "Senior": "is_senior_homeowners",
    "HOA Lien": "is_hoa_lien",
    "Other Lien": "is_other_lien",
    # Both live-verified 2026-09-23 via count-delta (POST /properties/search/):
    # standalone "custom" + min_match 1 clearly narrows the result set (not
    # silently ignored) -- Clay MO 85,132 -> 184 for bankruptcy, Jackson MO
    # 246,660 -> 6 for inheritance. Previously only "seen working inside a
    # larger stacked combo" per the skill's reference; now confirmed standalone.
    "Bankruptcy": "is_bankruptcy_property",
    "Inheritance": "is_inheritance_property",
    "Lis Pendens": "is_lis_pendens",
    "Final Judgment": "is_final_judgment",
    "Notice of Foreclosure": "is_notice_of_foreclosure",
    "Court Order": "is_court_order",
}
ZOMBIE_DISTRESSOR_CODES = [
    "is_notice_of_default", "is_lis_pendens", "is_final_judgment",
    "is_notice_of_foreclosure", "is_court_order",
]


class UnmappedComboError(RuntimeError):
    """A combo label component isn't in the mapping table. Per the skill's own
    rule: never guess a field name -- surface this and verify live (count-delta
    method) before adding it to the table."""


def fetch_state_shard(state: str) -> dict:
    """learn.datasift.ai intermittently resets the TLS handshake (verified
    live 2026-09-22, ~1 in 3 calls) regardless of User-Agent -- retry rather
    than treat one reset as the county/state being wrong."""
    fips = STATE_FIPS[state.upper()]
    req = urllib.request.Request(f"{CAPTURE_BASE}/{fips}.json", headers={"User-Agent": _UA})
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, ConnectionError) as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"capture-data fetch failed after retries: {last_exc}") from last_exc


def resolve_county(state: str, county_name: str) -> dict:
    """Match by the shard's own name/st fields -- never assume a 'County'
    suffix (parishes, boroughs, independent cities)."""
    shard = fetch_state_shard(state)
    want = county_name.strip().lower()
    for fips, entry in shard.items():
        name = (entry.get("name") or "").strip().lower()
        if name == want or name == want.replace(" county", ""):
            return entry
    raise KeyError(f"No county matching {county_name!r} in {state} shard")


def p1_rows(county_entry: dict, *, view: str = "lists") -> list[dict]:
    """priority==1 rows from variants.lists (the 'All lists' view -- the
    P1-shortcut convention always uses this, never variants.ai), in the
    shard's own order (already correct; never re-sort by dpd)."""
    rows = (county_entry.get("variants") or {}).get(view) or []
    return [r for r in rows if r.get("priority") == 1]


def resolve_combo_filters(label: str) -> dict:
    """Split a combo label on '+' and resolve each component. Raises
    UnmappedComboError on anything not in the table -- never guess."""
    parts = [p.strip() for p in label.split("+")]
    filters: dict = {}
    distressors: list[str] = []
    unmapped: list[str] = []

    if any(p.lower() == "zombie" for p in parts):
        filters["extra_vacant"] = True
        for code in ZOMBIE_DISTRESSOR_CODES:
            if code not in distressors:
                distressors.append(code)
        parts = [p for p in parts if p.lower() != "zombie"]

    for part in parts:
        m = AI_SCORE_RE.match(part)
        if m:
            filters["investor_off_market_score_min"] = int(m.group(1))
            filters["investor_off_market_score_max"] = int(m.group(2))
        elif part in SIMPLE_FIELD_MAP:
            filters.update(SIMPLE_FIELD_MAP[part])
        elif part.lower() in ("out-of-state", "out of state"):
            filters.update(OUT_OF_STATE)
        elif part in DISTRESSOR_CODES:
            code = DISTRESSOR_CODES[part]
            if code not in distressors:
                distressors.append(code)
        else:
            unmapped.append(part)

    if unmapped:
        raise UnmappedComboError(
            f"Component(s) {unmapped!r} of combo {label!r} are not in the mapping "
            "table -- verify live via count-delta before building this preset."
        )

    if distressors:
        filters["distressors_method"] = "custom"
        filters["distressors_include"] = ",".join(distressors)
        # AND semantics: a combo row means every listed signal present.
        filters["distressors_include_min_match"] = len(distressors)

    return filters


def county_address(fips: str, county_name: str, state: str, *, rich: bool) -> dict:
    title = f"{county_name} County, {state}"
    base = {"state": state, "title": title, "value": title, "county": county_name,
            "searchType": "county", "counties": [{"fips": fips, "county_name": county_name}]}
    if rich:
        return base
    return dict(base, search=county_name, type="county")
