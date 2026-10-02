"""Pre-call + post-call text drafting, written into the account's existing
LeadFlow Texts custom fields (not a generic Text Touch 1-4 set -- see the
plan's rationale: those fields already existed, purpose-built, unused).

COPY IS A FIRST DRAFT, not proven copy the way sms_agent.knowledge.touches's
pools are (those were reviewed and tuned; these were not). Read back the
single-record verification test before trusting this at scale, same
discipline as everything else in this build.
"""
from __future__ import annotations

import hashlib
import logging
import re

from ..knowledge import touches
from . import config as d25config

log = logging.getLogger(__name__)

# {first} may be "" (entity/initials-only owner) -- every variant must also
# read naturally without it (mirrors touches.py's *_NONAME pattern).
PRECALL = [
    "Hi {first}! This is {sender}, a local buyer here in {place}. I wanted to give you a"
    " heads up that I'll be calling about {addr} shortly. Talk soon!",
    "Hi {first}, {sender} here. I buy houses around {place} and wanted to text before I call"
    " you about {addr} in a bit. Looking forward to connecting!",
    "Hey {first}, this is {sender}. I'll be reaching out by phone shortly about {addr}."
    " Wanted to say hi first so my call doesn't come out of nowhere!",
]
PRECALL_NONAME = [
    "Hi! This is {sender}, a local buyer here in {place}. I wanted to give you a heads up"
    " that I'll be calling about {addr} shortly. Talk soon!",
    "Hi there, {sender} here. I buy houses around {place} and wanted to text before I call"
    " about {addr} in a bit. Looking forward to connecting!",
]
POSTCALL_LEFT_VOICEMAIL = [
    "Hi {first}, {sender} again. Just left you a voicemail about {addr}. No rush at all,"
    " call or text back whenever works for you!",
    "Hey {first}, sorry I missed you! This is {sender}, I left a message about {addr}."
    " Feel free to call or text back whenever you get a chance.",
]
POSTCALL_LEFT_VOICEMAIL_NONAME = [
    "Hi, {sender} again. Just left a voicemail about {addr}. No rush, call or text back"
    " whenever works for you!",
    "Hey there, sorry I missed you! This is {sender}, I left a message about {addr}."
    " Feel free to call or text back whenever suits you.",
]


def _place(city: str) -> str:
    return city or "the area"


def _pick(seed: str, salt: str, pool: list[str]) -> str:
    n = int(hashlib.md5(f"{salt}|{seed}".encode()).hexdigest(), 16)
    return pool[n % len(pool)]


def _identity(cand: dict) -> tuple[str, str]:
    """(first, seed) -- entities and initials-only owners never get "Hi FirstName"."""
    owner_full = cand.get("owner_company") or (
        f"{cand.get('owner_first', '')} {cand.get('owner_last', '')}"
    ).strip()
    first = "" if touches.is_entity(owner_full) else touches.clean_first(
        cand.get("owner_first") or owner_full
    )
    seed = f"{cand['street']}|{owner_full}".lower()
    return first, seed


def render_precall(cand: dict) -> str:
    first, seed = _identity(cand)
    pool = PRECALL if first else PRECALL_NONAME
    template = _pick(seed, "precall", pool)
    text = template.format(
        first=first, sender=d25config.WENDY_NAME, addr=cand["street"], place=_place(cand["city"])
    )
    return re.sub(r"\s+", " ", text).strip()


def render_postcall_left_voicemail(cand: dict) -> str:
    first, seed = _identity(cand)
    pool = POSTCALL_LEFT_VOICEMAIL if first else POSTCALL_LEFT_VOICEMAIL_NONAME
    template = _pick(seed, "postcall_vm", pool)
    text = template.format(
        first=first, sender=d25config.WENDY_NAME, addr=cand["street"], place=_place(cand["city"])
    )
    return re.sub(r"\s+", " ", text).strip()


def field_index(api) -> dict:
    """label -> field_uuid, same resolution pattern as datasift_api_upload.py."""
    r = api.call("/api/internal/custom-fields/", params={"limit": 999, "offset": 0})
    rows = r.get("results") or r.get("data") or []
    idx = {}
    for f in rows:
        label = f.get("label") or f.get("title")
        if label:
            idx[label] = f.get("uuid")
    return idx


def write_field(api, property_uuid: str, field_uuid: str, text: str) -> dict:
    if not field_uuid:
        return {}
    return api.call(
        f"/api/internal/property/{property_uuid}/custom-field/update-values/",
        method="PATCH",
        body=[{"field_uuid": field_uuid, "value": text}],
    )
