"""Content guardrails for a rendered email, before the compliance footer is
appended.

Deliberately a near-duplicate of `sms_agent.respond`'s regex family rather
than an import of its private names: the two surfaces already diverge (email
allows a longer body and a plain-text address in the footer; SMS blocks any
digit sequence that looks like a zip code, which the footer must contain on
purpose). `text-touch-builder` and `sms_agent/respond.py` already carry this
same "kept in sync by hand, not by import" relationship for the same reason.

Run BEFORE the footer is appended. The footer's physical address would
otherwise trip the zip-code / list-of-digits style checks that exist to keep
casual copy from reading like a mail-merge.
"""
from __future__ import annotations

import re

MAX_BODY_CHARS = 1500

_MONEY = re.compile(
    r"(\$\s*\d)"
    r"|(\b\d[\d,]*\s*(k|K)\b)"
    r"|(\b\d[\d,]*\s*(thousand|grand|million)\b)"
    r"|(\b(low|mid|high)\s+\d{2,3}s?\b)"
    r"|(\b\d{2,3}\s*[-to]{1,3}\s*\d{2,3}\s*(k|K)\b)",
)
_LINK = re.compile(r"(https?://)|(www\.)|(\b[\w-]+\.(com|net|org|io|co|us|info|link)\b)", re.I)
_BANNED = re.compile(
    r"\b(opportunity|solution|reach out|circle back|touch base|no obligation|"
    r"absolutely|certainly|leverage|utilize|elevate|seamless|unleash|delve|"
    r"streamline|robust|empower|tailored|curated|comprehensive|myriad|holistic|"
    r"synerg\w+|additionally|furthermore|moreover|nevertheless|"
    r"feel free to|do ?n[o']?t hesitate|at your earliest convenience)\b",
    re.I,
)
_FORM_LETTER = re.compile(r"\bI hope this (message|email) finds you well\b", re.I)
_SEMICOLON = re.compile(r";")
_SHOUTING = re.compile(r"!{2,}|\b[A-Z]{4,}\b")
_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")

# Same rule as the texts, same reason: the seller should feel found, not
# targeted. Naming the list is the fastest way to turn a friendly email into a
# spam complaint or a reply that reads the county data back at us.
_LIST_WORDS = re.compile(
    r"\b(foreclos\w*|auction\w*|probate|decedent|inherit\w*|estate sale|"
    r"tax (lien|sale|delinq\w*)|delinquen\w*|lien|code violation|condemn\w*|"
    r"evict\w*|divorce|bankrupt\w*|behind on (your )?(payment|mortgage|taxes)|"
    r"pre-?foreclosure|trustee sale|distress\w*|default)\b",
    re.I,
)


def validate(text: str, max_questions: int = 1) -> tuple[bool, list[str]]:
    """Hard gate on the BODY ONLY (no footer yet). Returns (ok, reasons)."""
    problems = []
    body = (text or "").strip()
    if not body:
        return False, ["empty"]
    if len(body) > MAX_BODY_CHARS:
        problems.append(f"too long ({len(body)} > {MAX_BODY_CHARS})")
    if _MONEY.search(body):
        problems.append("contains a dollar amount or price signal")
    if _LINK.search(body):
        problems.append("contains a link")
    hit = _LIST_WORDS.search(body)
    if hit:
        problems.append(f"names the list ('{hit.group(0)}'); the seller must feel found, not targeted")
    hit = _BANNED.search(body)
    if hit:
        problems.append(f"machine-written wording ('{hit.group(0)}')")
    if _FORM_LETTER.search(body):
        problems.append("form-letter opener")
    if _SEMICOLON.search(body):
        problems.append("semicolon")
    if _SHOUTING.search(body):
        problems.append("stacked exclamation marks or shouting in caps")
    if _EMOJI.search(body):
        problems.append("emoji")
    if "—" in body or "–" in body:
        problems.append("contains an em/en dash")
    if body.count("?") > max_questions:
        problems.append(f"asks more than {max_questions} question(s)")
    if re.search(r"\b(i am|i'm)\s+(an?\s+)?(ai|bot|automated)", body, re.I):
        problems.append("self-identifies as automated")
    return (not problems), problems
