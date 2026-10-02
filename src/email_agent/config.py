"""Configuration for the email cadence.

Deliberately thin. Everything that already has one correct answer in the SMS
agent (which board, who signs a record, what tag means "never contact again")
is imported from `sms_agent.config` rather than re-declared, so the two
channels cannot drift into disagreeing about either one.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")

# sms_agent lives one package over; make it importable the same way this
# package itself is imported (as `email_agent`, with `src/` on sys.path).
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
from sms_agent import config as sms_config  # noqa: E402


def _env(key: str, default: str = "") -> str:
    """See sms_agent.config._env: strips a stray CRLF that broke a header once."""
    value = os.getenv(key, default)
    return value.strip() if isinstance(value, str) else value


# ---------------------------------------------------------------- storage
DATA_DIR = Path(_env("EMAIL_AGENT_DATA_DIR", str(ROOT / "output" / "email_agent")))
DB_PATH = Path(_env("EMAIL_AGENT_DB", str(DATA_DIR / "email_agent.db")))

DRY_RUN = _env("EMAIL_AGENT_DRY_RUN", "1") not in ("0", "false", "False", "")

# ---------------------------------------------------------------- DataSift auth
# The email-integration surface (list/read/send through a connected mailbox)
# needs the minted user JWT, not the Open API key the SMS side runs on day to
# day (custom fields and other newer /api/internal/ surfaces 401 on the key on
# at least one account seen in this codebase; the JWT is the credential proven
# to reach all of it). Minted from the same DATASIFT_EMAIL/PASSWORD the upload
# and KPI scripts already use.
DATASIFT_EMAIL = _env("DATASIFT_EMAIL", "")
DATASIFT_PASSWORD = _env("DATASIFT_PASSWORD", "")
DATASIFT_BASE = _env("DATASIFT_API_BASE", "https://apiv2.reisift.io")

# Which connected mailbox to send through, when more than one is connected.
# Matched against the integration list by email address (case-insensitive
# substring); leave blank to use the only one, or the first one, and let
# `doctor` tell you what it picked.
EMAIL_INTEGRATION_ADDRESS = _env("EMAIL_AGENT_MAILBOX", "")
EMAIL_INTEGRATION_ID = _env("EMAIL_AGENT_MAILBOX_ID", "")

# ---------------------------------------------------------------- cohort
# Same board the texting agent works by default (config.CAMPAIGN_PRESET,
# "D x D"), so "who is in play" does not have to be maintained twice. Override
# with EMAIL_AGENT_CAMPAIGN_PRESET only if the email cadence should draw from a
# different preset than the texts.
CAMPAIGN_PRESET = _env("EMAIL_AGENT_CAMPAIGN_PRESET", sms_config.CAMPAIGN_PRESET)

# Whole days between one owner's touches. Longer than the SMS gap (1 day) on
# purpose: an inbox getting a new email from the "same person" daily reads as
# spam far faster than a phone getting a daily text, and email has no per-line
# daily cap forcing pacing the way a texting number does.
TOUCH_GAP_DAYS = int(_env("EMAIL_AGENT_TOUCH_GAP_DAYS", "4"))

# Sending cap per run. Conservative default: a freshly connected mailbox with
# no sending reputation gets throttled or spam-foldered by sending in bulk from
# day one, the email equivalent of the phone-number warm-up problem.
DAILY_CAP = int(_env("EMAIL_AGENT_DAILY_CAP", "150"))
# Seconds to sleep between sends within a run. Real pacing, not cosmetic: an
# API that fires 150 sends in ten seconds is exactly what a mailbox provider's
# abuse detector is built to catch.
SEND_SPACING_SECONDS = float(_env("EMAIL_AGENT_SEND_SPACING", "3"))

# ---------------------------------------------------------------- identity
# Same file the texting agent reads: assigned_to uuid -> first name. A record
# with no mapped assignee gets no email, same rule as no unsigned text.
SENDERS_FILE = sms_config.SENDERS_FILE

# Same tag the SMS agent writes on an opt-out ("Do Not Market"), so a person
# who unsubscribes from one channel is suppressed on both without a second
# tag to keep in sync.
TAG_OPT_OUT = sms_config.TAG_OPT_OUT
TAG_PREFIX = sms_config.TAG_PREFIX

# ---------------------------------------------------------------- CAN-SPAM
# The CAN-SPAM Act requires a valid physical postal address in every
# commercial email and a working opt-out mechanism honored promptly. Both are
# hard requirements, not house style, so a missing address blocks sending
# rather than silently shipping a non-compliant email (same posture as the SMS
# side refusing to invent a sender identity).
PHYSICAL_ADDRESS = _env("EMAIL_AGENT_PHYSICAL_ADDRESS", "")

# Where a real test send lands during `doctor` / before releasing a batch.
TEST_RECIPIENT = _env("EMAIL_AGENT_TEST_RECIPIENT", "")


def missing() -> list[str]:
    """Config gaps that would make a send fail, invalid, or non-compliant."""
    gaps = []
    if not DATASIFT_EMAIL or not DATASIFT_PASSWORD:
        gaps.append("DATASIFT_EMAIL / DATASIFT_PASSWORD (cannot mint a JWT to reach email-integration)")
    if not PHYSICAL_ADDRESS:
        gaps.append("EMAIL_AGENT_PHYSICAL_ADDRESS (CAN-SPAM requires one in every commercial email; sends are blocked without it)")
    if not TEST_RECIPIENT:
        gaps.append("EMAIL_AGENT_TEST_RECIPIENT (doctor cannot verify the send-email payload shape without somewhere to send a real test)")
    return gaps
