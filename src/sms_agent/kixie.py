"""Kixie client: outbound SMS, and translating Kixie webhooks into the event
shape the engine already speaks.

Added 2026-10 for the Creative Home Solutions deployment, which runs on Kixie
rather than smrtPhone. The engine never learns which vendor it is talking to:
`normalize_webhook` rewrites every Kixie payload into the smrtPhone-shaped
event (`smsIncoming` / `smsOutgoing` / `callEnded` with `from`, `to`,
`message`, `smsId`, `userName`, `source`), so classification, takeover
detection, suppression and the outbox all run unchanged.

What Kixie documents (support.kixie.com "Send SMS", "Team SMS", "Webhooks",
"Webhooks API"; developer.kixie.com text-message and end-call webhooks), and
what it does not:

  * Send: POST https://apig.kixie.com/app/event?apikey=<key>, JSON body with
    businessid, apikey, eventname, target, message. `eventname: "sms"` sends
    from the Kixie USER named by `email` (their Outbound SMS Number), so the
    FROM number is fixed per user and cannot be chosen per message.
    `eventname: "bizsms"` (Team SMS) takes a Team SMS `id` and an optional
    `fromNumber`, which is what a multi-number pool needs.
  * The send RESPONSE body is undocumented. A 2xx is treated as accepted
    unless the body says otherwise, and the raw body is logged so the first
    live send shows what Kixie actually returns.
  * Webhooks wrap the event in `data`. The SMS event fires for both
    directions (`direction: incoming|outgoing`); `customernumber` is the
    other party, `businessnumber` is ours. Published samples carry stray
    whitespace in both, so every number goes through clean_phone.
  * There is NO delivery-status webhook, NO message-history API to
    reconcile against, and NO payload signature (only an optional static
    header, which the deployment can add; the secret URL path is the gate).
  * Plan: Professional tier, and support must switch API access on.
"""
from __future__ import annotations

import logging
import time
from typing import Optional
from urllib.parse import unquote

import requests

from . import config, store
from .smrtphone import SendResult

log = logging.getLogger(__name__)

EVENT_URL = "https://apig.kixie.com/app/event"
AGENT_STATUS_URL = "https://apig.kixie.com/www/agent/status"


def _e164(n: str) -> str:
    digits = store.clean_phone(n)
    return f"+1{digits}" if len(digits) == 10 else (f"+{digits}" if digits else "")


def _body_says_failed(data) -> str:
    """The send response is undocumented, so read it defensively.

    Returns an error string when the body clearly reports a failure, else "".
    Anything ambiguous counts as success, because the HTTP status already did.
    """
    if not isinstance(data, dict):
        return ""
    if data.get("success") is False or str(data.get("status", "")).lower() in ("error", "failed"):
        return str(data.get("message") or data.get("error") or data)[:200]
    if data.get("error"):
        return str(data.get("error"))[:200]
    return ""


def _payload(to: str, body: str, from_number: str) -> dict:
    base = {
        "businessid": config.KIXIE_BUSINESS_ID,
        "apikey": config.KIXIE_API_KEY,
        "target": _e164(to),
        "message": body,
    }
    if config.KIXIE_TEAM_SMS_ID:
        base.update(eventname="bizsms", id=config.KIXIE_TEAM_SMS_ID)
        if from_number:
            # Team SMS examples write the number as bare digits with the 1.
            base["fromNumber"] = _e164(from_number).lstrip("+")
    else:
        base.update(eventname="sms", email=config.KIXIE_AGENT_EMAIL)
    return base


def send_sms(to: str, body: str, from_number: str = "", timeout: int = 20) -> SendResult:
    """Send one text. Returns a result rather than raising, always."""
    if not config.KIXIE_API_KEY or not config.KIXIE_BUSINESS_ID:
        return SendResult(False, error="KIXIE_API_KEY / KIXIE_BUSINESS_ID not set")
    if not config.KIXIE_TEAM_SMS_ID and not config.KIXIE_AGENT_EMAIL:
        return SendResult(False, error="KIXIE_AGENT_EMAIL (or KIXIE_TEAM_SMS_ID) not set")
    if not _e164(to):
        return SendResult(False, error=f"unusable destination {to!r}")

    payload = _payload(to, body, from_number)
    last = ""
    for attempt in range(3):
        try:
            resp = requests.post(
                EVENT_URL, params={"apikey": config.KIXIE_API_KEY}, json=payload, timeout=timeout
            )
        except requests.RequestException as exc:
            last = f"network: {exc}"
            time.sleep(2 ** attempt)
            continue

        text = resp.text[:300]
        if 200 <= resp.status_code < 300:
            try:
                data = resp.json()
            except ValueError:
                data = None
            failed = _body_says_failed(data)
            if failed:
                return SendResult(False, error=f"kixie refused: {failed}", status_code=400)
            sms_id = ""
            if isinstance(data, dict):
                inner = data.get("data") if isinstance(data.get("data"), dict) else data
                sms_id = str(inner.get("messageid") or inner.get("messageId") or inner.get("id") or "")
            log.info("kixie send accepted (HTTP %s): %s", resp.status_code, text)
            return SendResult(True, sms_id=sms_id, status_code=resp.status_code)

        last = f"HTTP {resp.status_code}: {text}"
        if 400 <= resp.status_code < 500 and resp.status_code != 429:
            return SendResult(False, error=last, status_code=resp.status_code)
        time.sleep(2 ** attempt)

    return SendResult(False, error=last or "send failed after 3 attempts")


def add_to_dnt(phone: str) -> tuple[bool, str]:
    """Kixie documents no do-not-text write API. Local suppression (applied by
    the caller regardless) is what actually honors the opt-out."""
    return False, "kixie has no documented do-not-text API; suppressed locally only"


def check_auth() -> tuple[bool, str]:
    """Credential probe for `doctor`. Cannot send anything.

    Uses the read-only Agent Status call, which needs the key AND a Kixie user
    email. With only a Team SMS id configured there is no safe probe, so it
    reports that rather than guessing.
    """
    if not config.KIXIE_API_KEY or not config.KIXIE_BUSINESS_ID:
        return False, "KIXIE_API_KEY / KIXIE_BUSINESS_ID not set"
    if not config.KIXIE_AGENT_EMAIL:
        return False, "no KIXIE_AGENT_EMAIL to probe with (Team SMS mode has no safe probe)"
    try:
        resp = requests.get(
            AGENT_STATUS_URL,
            params={"apiKey": config.KIXIE_API_KEY, "email": config.KIXIE_AGENT_EMAIL},
            timeout=15,
        )
    except requests.RequestException as exc:
        return False, f"network: {exc}"
    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code == 200 and isinstance(data, dict) and data.get("success") is not False:
        return True, "key accepted by agent status"
    return False, f"HTTP {resp.status_code}: {resp.text[:140]}"


# ------------------------------------------------------------------ webhooks

def _unwrap(payload: dict) -> dict:
    inner = payload.get("data")
    if isinstance(inner, dict):
        merged = dict(inner)
        # hookevent sometimes sits beside `data` rather than inside it.
        for key in ("hookevent", "customernumber", "businessnumber"):
            if key not in merged and key in payload:
                merged[key] = payload[key]
        return merged
    return payload


def _num(value) -> str:
    digits = store.clean_phone(str(value or ""))
    return _e164(digits) if digits else ""


# Kixie's published disposition sample carries the business's CRM integration
# credentials inside `activeCRM`. The event log keeps raw payloads forever, so
# anything shaped like a credential is dropped before it is stored.
_SECRET_KEYS = {"activecrm", "token", "accesstoken", "access_token", "refreshtoken",
                "refresh_token", "apikey", "api_key", "password", "webhookheaders"}


def _scrub(value):
    if isinstance(value, dict):
        return {k: ("[removed]" if str(k).lower() in _SECRET_KEYS else _scrub(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def normalize_webhook(payload: dict) -> dict:
    """Kixie webhook -> the engine's event shape.

    Unrecognized events come back as `kixie:<hookevent>`, which the engine has
    no handler for, so they are stored and ignored rather than guessed at.
    The original payload, minus anything credential-shaped, rides along under
    `_kixie` for the event log.
    """
    d = _unwrap(payload)
    hook = str(d.get("hookevent") or payload.get("hookevent") or "").strip().lower()
    out: dict = {"_kixie": _scrub(payload)}

    if hook == "sms" or (not hook and "direction" in d and "message" in d):
        direction = str(d.get("direction") or "").strip().lower()
        customer = _num(d.get("customernumber"))
        business = _num(d.get("businessnumber"))
        email = unquote(str(d.get("email") or "")).strip().lower()
        out.update(
            smsId=str(d.get("messageid") or d.get("messageId") or ""),
            message=str(d.get("message") or ""),
            date=str(d.get("messageDate") or ""),
        )
        if direction == "incoming":
            out.update(event="smsIncoming",
                       **{"from": customer or _num(d.get("from")), "to": business or _num(d.get("to"))})
        elif direction == "outgoing":
            out.update(event="smsOutgoing",
                       **{"to": customer or _num(d.get("to")), "from": business or _num(d.get("from"))})
            # Our own API sends come back through this same webhook under the
            # API user's email. Report those as the API surface, so takeover
            # detection falls through to the send ledger and body match, the
            # same path smrtPhone API sends take. Any OTHER Kixie user typing
            # into the thread is a named human, which is exactly the signal
            # that must pause the agent.
            api_user = config.KIXIE_AGENT_EMAIL.lower()
            if email and email != api_user:
                out.update(source="kixie-app", userName=email)
            else:
                out.update(source="api", userName="")
        else:
            out["event"] = f"kixie:sms:{direction or 'unknown'}"
        return out

    if hook in ("endcall", "disposition"):
        details = d.get("callDetails") if isinstance(d.get("callDetails"), dict) else {}
        customer = _num(d.get("customernumber") or d.get("phone"))
        try:
            secs = int(float(details.get("duration") or 0))
        except (TypeError, ValueError):
            secs = 0
        out.update(
            event="callEnded" if hook == "endcall" else "kixie:disposition",
            callId=str(details.get("callid") or d.get("callid") or ""),
            phone=customer,
            duration=secs,
            user=unquote(str(details.get("email") or d.get("email") or "")).strip(),
            status=str(details.get("callstatus") or ""),
            disposition=str(details.get("disposition") or d.get("disposition") or ""),
        )
        return out

    out["event"] = f"kixie:{hook or 'unknown'}"
    return out
