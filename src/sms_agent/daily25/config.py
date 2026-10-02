"""Config for the daily 35: boards, scoring weights, verified field/option
uuids, Wendy's alternated numbers, cooldown, and the three schedule times.

Everything is env-driven, same convention as sms_agent/config.py, so a
default below can be overridden with a Fly secret/env var without a code
change. Several values are hardcoded VERIFIED CONSTANTS (Wendy's uuid, the
Call Disposition field/option uuids) rather than resolved by name at runtime,
because they are decision inputs this pipeline branches on -- a renamed field
should be a loud --doctor miss, not a silently wrong branch.
"""
from __future__ import annotations

import os


def _env(key: str, default: str = "") -> str:
    value = os.getenv(key, default)
    return value.strip() if isinstance(value, str) else value


# ---------------------------------------------------------------- boards
# Verified live board uuids (Ty, 2026-08-27).
BOARD_ACQUISITIONS = _env("DAILY25_BOARD_ACQUISITIONS", "70ba2eec-6eca-4d20-bf8a-8b4367ce278c")
BOARD_LEAD_FLOW = _env("DAILY25_BOARD_LEAD_FLOW", "8b97827a-968a-4383-a1d1-6852d10ee081")
BOARDS = [
    ("Acquisitions", BOARD_ACQUISITIONS),
    ("Lead Flow", BOARD_LEAD_FLOW),
]

# ---------------------------------------------------------------- picks
DAILY_PICK_COUNT = int(_env("DAILY25_COUNT", "35"))
# Dwell (days in current column) that maxes out the staleness half of the
# urgency score. Column position alone is used when the timeline endpoint
# hasn't computed a value yet (see scoring._urgency) -- never guess a dwell.
STALE_DAYS = float(_env("DAILY25_STALE_DAYS", "6"))

# Weighted blend: pipeline urgency (don't let the board rot) vs lead economics
# (make Wendy's calls worth taking). 50/50 by default, named so a future tune
# is one number, not an archaeology dig through scoring.py.
URGENCY_WEIGHT = float(_env("DAILY25_URGENCY_WEIGHT", "0.5"))
QUALITY_WEIGHT = float(_env("DAILY25_QUALITY_WEIGHT", "0.5"))

DEAD_STATUSES = {
    s.strip().lower()
    for s in _env("DAILY25_DEAD_STATUSES", "sold,not_interested,dead,do_not_contact").split(",")
    if s.strip()
}

# A record already texted a pre-call message within this many days is
# excluded from being texted again if it resurfaces in a later day's top-35.
# It can still be picked/scored/tasked -- this blocks the TEXT, not the call.
COOLDOWN_DAYS = int(_env("DAILY25_COOLDOWN_DAYS", "3"))
PRECALL_INTENT = "daily25_precall"

# ---------------------------------------------------------------- Wendy
WENDY_EMAIL = _env("DAILY25_WENDY_EMAIL", "team-member@example.com")
WENDY_NAME = _env("DAILY25_WENDY_NAME", "")
# GET /api/internal/user/ is "retrieveUser" -- it returns the AUTHENTICATED
# user (the DATASIFT_EMAIL account owner), not a team-member list, and
# reisift publishes no documented user-list endpoint (same gap
# config/sms_senders.json's own note describes). Verified live 2026-08-27 by
# scanning both boards' cards for an assigned_to match on WENDY_EMAIL --
# tasks.scan_boards_for_assignee() re-confirms this in --doctor.
WENDY_UUID = _env("DAILY25_WENDY_UUID", "")
# Alternated strictly for the 09:00 send, NOT routed by lead state.
WENDY_NUMBERS = [
    _env("DAILY25_WENDY_NUMBER_1", "+15555550101"),
    _env("DAILY25_WENDY_NUMBER_2", "+15555550102"),
]
# Minutes between consecutive sends in the 09:00/18:00 stagger (~2min * 35
# records spans roughly an hour, matching the spec's 09:00-10:10 window).
SEND_STAGGER_SECONDS = int(_env("DAILY25_SEND_STAGGER_SECONDS", "120"))

# ---------------------------------------------------------------- DataSift fields
# Verified live 2026-08-27: this account already has purpose-built fields for
# exactly this (group "LeadFlow Texts", created 2026-07-23, wired to no code
# until now) and a real Call Disposition select field (group "Appointment &
# Next Steps"). Resolved by label at runtime via texting.field_index() --
# only the two used as DECISION inputs (not just write targets) are pinned.
PRECALL_FIELD_LABEL = "LeadFlow Pre-Call Text"
POSTCALL_FIELD_LABELS = {
    "Left Voicemail": "LeadFlow Post-Call Text (Left Voicemail)",
    "Answered": "LeadFlow Post-Call Text (Answered)",
    "No Answer": "LeadFlow Post-Call Text (No Answer)",
    "Requested Callback": "LeadFlow Post-Call Text (Requested Callback)",
    "Not Interested": "LeadFlow Post-Call Text (Not Interested)",
}
CALL_DISPOSITION_FIELD_UUID = _env(
    "DAILY25_CALL_DISPOSITION_FIELD", "329d42c0-0d80-43c2-9b5b-e81b09e3a7c8"
)
DISPOSITION_LEFT_VOICEMAIL_OPTION_UUID = _env(
    "DAILY25_DISPOSITION_LEFT_VOICEMAIL_OPTION", "6afaf4a2-e957-4365-9ccf-735285d299fe"
)
# The one disposition this build actually wires a follow-up run to; the other
# POSTCALL_FIELD_LABELS resolve and are logged so a future run can use them.
VOICEMAIL_FOLLOWUP_DISPOSITION = "Left Voicemail"

# ---------------------------------------------------------------- phone scoring
# Never re-score an already-tagged number, and no separate bulk backfill --
# only score a candidate phone that (a) is in today's top-35 and (b) carries
# none of these tier tags yet (see phones.py).
DIAL_TIER_NAMES = {"Dial First", "Dial Second", "Dial Third", "Dial Fourth", "Drop"}
TRESTLE_API_KEY = _env("TRESTLE_API_KEY", "")

# ---------------------------------------------------------------- idempotency
TAG_PREFIX = "sys_daily25_"


def today_tag(today_iso: str) -> str:
    return f"{TAG_PREFIX}{today_iso}"


# ---------------------------------------------------------------- scheduling
# Off until --doctor and a verified single-record --commit of all 3 stages
# have been run by hand. Flipping this on is the deliberate go-live step.
ENABLED = _env("DAILY25_ENABLED", "0") not in ("0", "false", "no")
# Texting toggles independently of selection: Ty asked to pause texting
# 2026-08-31 over a cadence issue while still wanting the daily pick + call
# tasks to keep running. Off means "select" still fires on schedule; "send"
# and "voicemail" (the only two stages that text) do not. Default OFF until
# explicitly turned back on.
TEXTING_ENABLED = _env("DAILY25_TEXTING_ENABLED", "0") not in ("0", "false", "no")
# Hour-threshold gates (business-local), same granularity/pattern as the
# existing campaign scheduler (config.CAMPAIGN_START_HOUR): the worker polls
# every WORKER_INTERVAL seconds, so each stage fires on the first poll after
# its threshold and is gated to once per day by store.mark_notified -- a
# to-the-minute scheduler buys nothing here and running one alongside the
# existing 20s poll loop would be two scheduling paradigms for one job.
SELECT_HOUR = int(_env("DAILY25_SELECT_HOUR", "8"))
SEND_HOUR = int(_env("DAILY25_SEND_HOUR", "9"))
VOICEMAIL_HOUR = int(_env("DAILY25_VOICEMAIL_HOUR", "18"))
SCHEDULE_DAYS = _env("DAILY25_DAYS", "0,1,2,3,4")  # Mon-Fri, same convention as CAMPAIGN_DAYS
# Falls back to the campaign timezone already configured in fly.toml
# (Wendy's numbers are Kansas City-area) rather than requiring a second knob.
BUSINESS_TIMEZONE = _env("DAILY25_TIMEZONE") or _env("SMS_AGENT_CAMPAIGN_TZ", "America/Chicago")
