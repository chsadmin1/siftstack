"""The three-touch email pool.

Same voice and same hard rules as the SMS touches (`sms_agent.knowledge.touches`,
the text-touch-builder skill's recipe): warm, first-name only, never name the
list, no dollar figure, no company name, sign with the assigned rep's first
name. Name hygiene (`clean_first`, `is_entity`) is imported rather than
reimplemented so an initials-only or entity owner is handled identically on
both channels; that exact bug shipped separately in two other places in this
codebase before it was fixed in both, so a shared import is worth the coupling.

Three touches, not four, and spaced further apart (config.TOUCH_GAP_DAYS,
default 4 days vs the SMS agent's 1): an inbox reads a same-sender cadence as
spam much faster than a phone does, and this channel has no per-line daily cap
forcing pacing the way texting does.

The footer (physical address + opt-out line, both CAN-SPAM requirements) is
NOT part of these templates. It is appended once, after validate(), by
`seed.render_email()`, so the body copy stays free of the digit strings and
punctuation that would otherwise look like machine-generated boilerplate.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))
from sms_agent.knowledge.touches import clean_first, is_entity  # noqa: E402

__all__ = ["clean_first", "is_entity", "render"]

# Touch 1: a warm, low-pressure introduction. Touch 2: a soft follow-up
# assuming the first landed. Touch 3: the gentle close, an easy out. Each job
# stays in its own touch, same discipline as the SMS pool.
SUBJECT1 = [
    "quick question about {street}",
    "{street} - is this yours?",
    "a question about your house on {street}",
]
BODY1 = [
    (
        "Hi {first},\n\n"
        "My name is {sender}, and I hope you don't mind the email out of the blue. "
        "I was looking into the property at {street} in {city} and wanted to check, is that one still yours?\n\n"
        "I buy a few houses a year in the area, and if you ever thought about selling I'd love to be the first call. "
        "No pressure either way, just wanted to reach out.\n\n"
        "Thanks so much,\n{sender}"
    ),
    (
        "Hi {first},\n\n"
        "I'm {sender}. I know an email from someone you don't know is a little random, so I'll keep this short. "
        "I came across {street} in {city} and was hoping to confirm it's your property.\n\n"
        "If it is, and selling is ever something you'd consider down the road, I'd genuinely love to be the person you talk to first.\n\n"
        "Have a great week,\n{sender}"
    ),
]
SUBJECT1_NONAME = [
    "a question about {street}",
    "{street} - checking in",
]
BODY1_NONAME = [
    (
        "Hello,\n\n"
        "My name is {sender}. I was hoping to reach whoever handles the property at {street} in {city}, "
        "and wanted to check whether that's you.\n\n"
        "I buy a few houses a year in the area, and if selling that one is ever on your mind, "
        "I'd love to be the first call. No pressure at all.\n\n"
        "Thanks so much,\n{sender}"
    ),
]

SUBJECT2 = [
    "following up on {street}",
    "checking back in - {street}",
]
BODY2 = [
    (
        "Hi {first},\n\n"
        "{sender} again. I emailed a little while back about {street} and wanted to check whether it made it through, "
        "these things have a way of landing in spam.\n\n"
        "If you've ever thought about selling it, I'd love to hear from you, even just to say not right now.\n\n"
        "Take care,\n{sender}"
    ),
    (
        "Hi {first},\n\n"
        "Just floating my last note back up in case it got buried. I'm still interested in {street} if you're ever open to a conversation about it.\n\n"
        "No worries either way,\n{sender}"
    ),
]
SUBJECT2_NONAME = SUBJECT2
BODY2_NONAME = [
    (
        "Hello again,\n\n"
        "{sender} here. I emailed a little while back about {street} and wanted to check whether it made it through.\n\n"
        "If you're the right person for that property and ever open to a conversation, I'd love to hear from you.\n\n"
        "Take care,\n{sender}"
    ),
]

SUBJECT3 = [
    "last note about {street}",
    "one more note on {street}",
]
BODY3 = [
    (
        "Hi {first},\n\n"
        "I don't want to keep filling your inbox, so this is the last note from me for now. "
        "If {street} is ever something you'd like to talk about, my door stays open, just reply any time.\n\n"
        "Wishing you the best,\n{sender}"
    ),
    (
        "Hi {first},\n\n"
        "Last one from me on this. If selling {street} ever comes up down the road, I'd still love to be your first call. "
        "If not, no hard feelings at all, take care of yourself.\n\n"
        "{sender}"
    ),
]
SUBJECT3_NONAME = SUBJECT3
BODY3_NONAME = [
    (
        "Hello,\n\n"
        "Last note from me for now. If {street} is ever something you'd like to discuss, feel free to reply any time.\n\n"
        "All the best,\n{sender}"
    ),
]

POOLS = [
    (SUBJECT1, BODY1, SUBJECT1_NONAME, BODY1_NONAME),
    (SUBJECT2, BODY2, SUBJECT2_NONAME, BODY2_NONAME),
    (SUBJECT3, BODY3, SUBJECT3_NONAME, BODY3_NONAME),
]


def render(touch: int, seed: str, first: str, street: str, city: str, sender: str) -> tuple[str, str]:
    """(subject, body) for one touch (1-3). `seed` makes selection deterministic
    per record, and different from a neighboring record, same as the SMS pool."""
    if not 1 <= touch <= 3:
        raise ValueError("touch must be 1-3")
    subj_pool, body_pool, subj_noname, body_noname = POOLS[touch - 1]
    subjects, bodies = (subj_pool, body_pool) if first else (subj_noname, body_noname)
    n = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    subject = subjects[(n // (7 ** (touch - 1))) % len(subjects)]
    body = bodies[(n // (11 ** (touch - 1))) % len(bodies)]
    fmt = dict(first=first, street=street, city=city or "the area", sender=sender)
    subject = re.sub(r"\s+", " ", subject.format(**fmt)).strip()
    body = body.format(**fmt).strip()
    return subject, body


# ---------------------------------------------------------------- farewell
#
# The email leg of the "DxD Exhausted" cadence (src/exhausted_cadence.py),
# paired with sms_agent.knowledge.touches.FAREWELL_POOLS on the days the
# schedule sends both channels. Same reasoning as that module's docstring:
# a separate arc from TOUCH1-3 above (winding down, not opening), scheduled
# by calendar day since the record entered the Exhausted column rather than
# by "next touch not yet sent", so it gets its own pools and render function.
#
# Three days carry an email (7, 9, 13): day 7 is a genuine last-chance
# check-in, day 9 softens toward closure, and day 13 is the explicit close,
# using Phil's own language ("wish I could have gotten a hold of you") as
# the actual goodbye rather than repeating it across every touch.
SUBJECT_F7 = ["checking in on {street}", "before I close out my notes on {street}"]
BODY_F7 = [
    (
        "Hi {first},\n\n"
        "It's been a little while since I first reached out about {street}, and I wanted to check in "
        "one more time before I close out my notes on it.\n\n"
        "If you're ever open to a conversation about it, even down the road, I'd still love to hear from you. "
        "If not, no worries at all.\n\n"
        "Take care,\n{sender}"
    ),
]
SUBJECT_F7_NONAME = ["a check-in on {street}"]
BODY_F7_NONAME = [
    (
        "Hello,\n\n"
        "It's been a little while since I first reached out about {street}, and I wanted to check in "
        "once more before I close out my notes on it.\n\n"
        "If you're ever open to a conversation about it, I'd still love to hear from you. If not, no worries at all.\n\n"
        "Take care,\n{sender}"
    ),
]

SUBJECT_F9 = ["still thinking about {street}", "one more check-in on {street}"]
BODY_F9 = [
    (
        "Hi {first},\n\n"
        "{sender} again. I know I've reached out a couple times about {street} without hearing back, "
        "and I don't want to keep filling your inbox.\n\n"
        "If the timing just isn't right, that's completely okay, I just didn't want to lose touch. "
        "Whenever you're ready, I'm here.\n\n"
        "All the best,\n{sender}"
    ),
]
SUBJECT_F9_NONAME = SUBJECT_F9
BODY_F9_NONAME = [
    (
        "Hello again,\n\n"
        "{sender} here. I've reached out a couple times about {street} without hearing back, "
        "and I don't want to keep filling your inbox.\n\n"
        "If the timing just isn't right, that's completely okay. Whenever you're ready, I'm here.\n\n"
        "All the best,\n{sender}"
    ),
]

SUBJECT_F13 = ["closing out my notes on {street}", "last note on {street}"]
BODY_F13 = [
    (
        "Hi {first},\n\n"
        "This is my last note on {street}. Sorry we weren't able to connect and get something worked out, "
        "I really wish I could have gotten a hold of you.\n\n"
        "If anything ever changes down the road, my door stays open, just reply any time. "
        "Wishing you all the best.\n\n{sender}"
    ),
]
SUBJECT_F13_NONAME = SUBJECT_F13
BODY_F13_NONAME = [
    (
        "Hello,\n\n"
        "This is my last note on {street}. Sorry we weren't able to connect and get something worked out, "
        "I wish I could have gotten a hold of the right person.\n\n"
        "If anything ever changes down the road, just reply any time. Wishing you all the best."
    ),
]

FAREWELL_POOLS = {
    7: (SUBJECT_F7, BODY_F7, SUBJECT_F7_NONAME, BODY_F7_NONAME),
    9: (SUBJECT_F9, BODY_F9, SUBJECT_F9_NONAME, BODY_F9_NONAME),
    13: (SUBJECT_F13, BODY_F13, SUBJECT_F13_NONAME, BODY_F13_NONAME),
}


def render_farewell(day: int, seed: str, first: str, street: str, city: str, sender: str) -> tuple[str, str]:
    """(subject, body) for the farewell arc's given schedule day (7, 9 or 13)."""
    if day not in FAREWELL_POOLS:
        raise ValueError(f"no farewell copy for day {day}; must be one of {sorted(FAREWELL_POOLS)}")
    subj_pool, body_pool, subj_noname, body_noname = FAREWELL_POOLS[day]
    subjects, bodies = (subj_pool, body_pool) if first else (subj_noname, body_noname)
    n = int(hashlib.md5(f"farewell{day}|{seed}".encode()).hexdigest(), 16)
    subject = subjects[n % len(subjects)]
    body = bodies[(n // 11) % len(bodies)]
    fmt = dict(first=first, street=street, city=city or "the area", sender=sender)
    subject = re.sub(r"\s+", " ", subject.format(**fmt)).strip()
    body = body.format(**fmt).strip()
    return subject, body
