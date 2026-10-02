"""DataSift's own email integration: list a connected mailbox, send through it,
read it back for the opt-out scan.

Auth mints a user JWT from DATASIFT_EMAIL/DATASIFT_PASSWORD via POST
/api/token/ (same pattern as datasift_api_upload.py and siftline_kpi.py), not
the Open API key the SMS side's crm_standalone.py runs on. That distinction is
deliberate: this codebase has already seen the Open API key 401 on newer
/api/internal/ surfaces on at least one account (custom fields, per
datasift_api_upload.py's own docstring) while the minted JWT reached them, and
email-integration is exactly that kind of newer surface.

THE send-email PAYLOAD SHAPE IS UNVERIFIED. `10-integrations.md` documents the
path (`POST /api/internal/email-integration/{id}/send-email/`) but not its
body. `send_email()` guesses the common shape and raises with the server's
full error text on a 4xx so a live test immediately shows the real field
names, rather than failing silently or guessing forever. Run `cli.py doctor`
and send yourself one real test email before releasing any batch, the same
discipline `datasift_api_upload.py` uses for custom-field writes ("always
upload one record and read it back before releasing the file").
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from . import config

log = logging.getLogger(__name__)

_JWT_REFRESH_SECONDS = 1800


class MailerError(RuntimeError):
    pass


class Api:
    """Thin JWT-authed client, mirroring datasift_api_upload.Api."""

    def __init__(self) -> None:
        if not config.DATASIFT_EMAIL or not config.DATASIFT_PASSWORD:
            raise MailerError(
                "DATASIFT_EMAIL / DATASIFT_PASSWORD not set; the email agent mints "
                "its own JWT from them and cannot reach email-integration without it"
            )
        self._base = config.DATASIFT_BASE.rstrip("/")
        self._session = requests.Session()
        self.token = ""
        self._minted = 0.0
        self._mint()

    def _mint(self) -> None:
        resp = self._session.post(
            self._base + "/api/token/",
            json={"email": config.DATASIFT_EMAIL, "password": config.DATASIFT_PASSWORD},
            timeout=45,
        )
        if resp.status_code >= 400:
            raise MailerError(f"could not mint a JWT (HTTP {resp.status_code}): {resp.text[:200]}")
        self.token = resp.json()["access"]
        self._minted = time.time()

    def call(self, path: str, method: str = "GET", body: Optional[dict] = None,
              params: Optional[dict] = None, _retry: bool = True) -> dict:
        if time.time() - self._minted > _JWT_REFRESH_SECONDS:
            self._mint()
        resp = self._session.request(
            method, self._base + path, json=body, params=params,
            headers={"Authorization": f"Bearer {self.token}"}, timeout=60,
        )
        if resp.status_code == 401 and _retry:
            self._mint()
            return self.call(path, method, body, params, _retry=False)
        if resp.status_code >= 400:
            raise MailerError(f"HTTP {resp.status_code} on {method} {path}: {resp.text[:400]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}


_api: Optional[Api] = None


def client() -> Optional[Api]:
    global _api
    if _api is not None:
        return _api
    try:
        _api = Api()
    except MailerError as exc:
        log.warning("email-integration client unavailable: %s", exc)
        return None
    return _api


# ---------------------------------------------------------------- mailboxes

def list_integrations() -> list[dict]:
    """Every connected mailbox on the account. Empty means none are connected
    yet — that is a DataSift Settings > Integrations step, not a code fix."""
    c = client()
    if not c:
        return []
    resp = c.call("/api/internal/email-integration/", params={"limit": 50})
    if isinstance(resp, list):
        return resp
    rows = resp.get("results") or resp.get("data") or []
    return rows if isinstance(rows, list) else []


def resolve_mailbox(preferred: str = "") -> Optional[dict]:
    """The mailbox to send through: EMAIL_AGENT_MAILBOX_ID, else a name/address
    match against EMAIL_AGENT_MAILBOX, else the only one, else the first."""
    if config.EMAIL_INTEGRATION_ID:
        for row in list_integrations():
            if str(row.get("id") or row.get("uuid")) == config.EMAIL_INTEGRATION_ID:
                return row
        return None

    rows = list_integrations()
    if not rows:
        return None
    want = (preferred or config.EMAIL_INTEGRATION_ADDRESS or "").strip().lower()
    if want:
        for row in rows:
            addr = str(row.get("email") or row.get("address") or row.get("account") or "").lower()
            if want in addr:
                return row
        return None
    return rows[0]


# -------------------------------------------------------------------- send

def send_email(integration_id: str, to_email: str, subject: str, body_text: str,
                reply_to: str = "") -> dict:
    """POST .../send-email/. Body shape is a best guess — see module docstring."""
    c = client()
    if not c:
        raise MailerError("no email-integration client (JWT mint failed)")
    payload = {"to": to_email, "subject": subject, "body": body_text}
    if reply_to:
        payload["reply_to"] = reply_to
    return c.call(
        f"/api/internal/email-integration/{integration_id}/send-email/",
        method="POST", body=payload,
    )


# -------------------------------------------------------------------- read

def read_email(integration_id: str, limit: int = 50) -> list[dict]:
    """Recent messages in the connected mailbox, for the opt-out scan."""
    c = client()
    if not c:
        return []
    resp = c.call(
        f"/api/internal/email-integration/{integration_id}/read-email/",
        params={"limit": limit},
    )
    if isinstance(resp, list):
        return resp
    rows = resp.get("results") or resp.get("data") or []
    return rows if isinstance(rows, list) else []


def doctor() -> dict:
    """Everything `cli.py doctor` needs to report, in one read-only pass."""
    out: dict = {"jwt": False, "integrations": [], "chosen": None, "error": ""}
    try:
        c = client()
        out["jwt"] = c is not None
    except MailerError as exc:
        out["error"] = str(exc)
        return out
    try:
        rows = list_integrations()
    except MailerError as exc:
        out["error"] = str(exc)
        return out
    out["integrations"] = [
        {"id": r.get("id") or r.get("uuid"), "address": r.get("email") or r.get("address")}
        for r in rows
    ]
    chosen = resolve_mailbox()
    if chosen:
        out["chosen"] = {"id": chosen.get("id") or chosen.get("uuid"),
                          "address": chosen.get("email") or chosen.get("address")}
    return out
