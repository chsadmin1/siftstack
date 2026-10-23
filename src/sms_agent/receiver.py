"""HTTP receiver for smrtPhone and DataSift webhooks.

Design rule: verify, persist, return 200, and do nothing slow. smrtPhone
documents no retry semantics and no signature, and it purges its own logs after
30 days, so a handler that blocks on an LLM call or a CRM round trip is how you
silently lose events. Everything downstream runs in the worker.

    uvicorn src.sms_agent.receiver:app --host 0.0.0.0 --port 8080

Endpoints (the secret is in the path because neither vendor signs its payload):
    POST /hooks/{secret}/smrtphone
    POST /hooks/{secret}/datasift
    GET  /hooks/{secret}/calls?since=&until=   (smrtPhone call log, for coaching)
    POST /hooks/{secret}/smrtphone-session     (upload a saved smrtPhone login)
    GET  /hooks/{secret}/smrtphone-session     (does the saved login still work?)
    POST /hooks/{secret}/canary?to=<team phone>  (one go-live test text)
    GET  /health
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from . import config, store

log = logging.getLogger(__name__)
app = FastAPI(title="SiftStack SMS agent", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _startup() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    store.init()
    gaps = config.missing()
    if gaps:
        log.warning("config gaps at startup: %s", "; ".join(gaps))
    log.info(
        "receiver up | phase=%s dry_run=%s db=%s", config.PHASE, config.DRY_RUN, config.DB_PATH
    )
    if config.INLINE_WORKER:
        _start_inline_worker()


def _start_inline_worker() -> None:
    """Run the worker loop in a thread inside the receiver process.

    Deliberate for a single-box deployment: SQLite is a single-writer file, and
    splitting the receiver and worker across two machines would mean two
    machines wanting the same volume. One process, one volume, no coordination
    problem. Split them only when the volume moves to a real database.
    """
    import threading

    from .worker import run_forever

    thread = threading.Thread(
        target=run_forever, kwargs={"interval": config.WORKER_INTERVAL}, daemon=True,
        name="sms-agent-worker",
    )
    thread.start()
    log.info("inline worker started (interval %ss)", config.WORKER_INTERVAL)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def _authorized(secret: str, request: Request) -> bool:
    if not config.WEBHOOK_SECRET or secret != config.WEBHOOK_SECRET:
        return False
    if config.ALLOWED_IPS and _client_ip(request) not in config.ALLOWED_IPS:
        log.warning("rejected webhook from %s (not in allowlist)", _client_ip(request))
        return False
    return True


def _dedupe_key(source: str, payload: dict) -> str:
    """Stable per event, not per delivery, so a retry collapses onto the first."""
    event = str(payload.get("event") or "")
    for key in ("smsId", "callId", "dialerCallId", "id"):
        if payload.get(key):
            return f"{source}:{event}:{payload[key]}"
    # No natural id (DNC/DNT, spam-list, DataSift). Fall back to the content.
    parts = [source, event]
    for key in ("phone", "from", "to", "timestamp", "date", "uuid", "message"):
        if payload.get(key):
            parts.append(str(payload[key])[:120])
    return ":".join(parts)


def _ingest(source: str, payload: dict, background: BackgroundTasks) -> dict:
    event_type = str(payload.get("event") or source)
    key = _dedupe_key(source, payload)
    event_id = store.record_event(source, event_type, key, payload)
    if event_id is None:
        log.info("duplicate %s (%s) ignored", event_type, key[:80])
        return {"ok": True, "duplicate": True}

    # Hand off to the worker. INLINE mode is for single-box deployments where
    # running a second process is not worth it; the 200 still returns first
    # because FastAPI runs background tasks after the response is sent.
    background.add_task(_process, event_id)
    return {"ok": True, "event_id": event_id}


def _process(event_id: int) -> None:
    rows = [r for r in store.pending_events(limit=200) if r["id"] == event_id]
    if not rows:
        return
    from .worker import process_event  # local import keeps startup light

    process_event(dict(rows[0]))


@app.post("/hooks/{secret}/smrtphone")
async def smrtphone_hook(
    secret: str, request: Request, background: BackgroundTasks
) -> Any:
    if not _authorized(secret, request):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed body, log the raw form instead
        raw = (await request.body()).decode("utf-8", "replace")
        store.record_event("smrtphone", "unparseable", f"raw:{hash(raw)}", {"raw": raw[:4000]})
        return {"ok": True, "note": "unparseable body logged"}
    if not isinstance(payload, dict):
        return {"ok": True, "note": "non-object payload ignored"}
    return _ingest("smrtphone", payload, background)


@app.post("/hooks/{secret}/datasift")
async def datasift_hook(secret: str, request: Request, background: BackgroundTasks) -> Any:
    if not _authorized(secret, request):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raw = (await request.body()).decode("utf-8", "replace")
        store.record_event("datasift", "unparseable", f"raw:{hash(raw)}", {"raw": raw[:4000]})
        return {"ok": True, "note": "unparseable body logged"}
    if not isinstance(payload, dict):
        payload = {"payload": payload}
    return _ingest("datasift", payload, background)


@app.get("/hooks/{secret}/calls")
def smrtphone_calls(secret: str, request: Request, since: str, until: str) -> Any:
    """smrtPhone call log for the coaching job (src/call_coaching/chs_daily_audit.py).

    Read-only, behind the same secret path as the webhooks, because it carries
    seller phone numbers and recording links. since/until are UTC ISO stamps.
    Answers 503 with the reason when the saved smrtPhone login is missing or
    expired, so the coaching job can say so in Slack instead of guessing.
    """
    if not _authorized(secret, request):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    from . import call_log

    try:
        return {"ok": True, "calls": call_log.calls_between(since, until)}
    except call_log.SessionError as exc:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={
            "ok": False, "error": "smrtphone_session",
            "detail": f"smrtPhone login is missing or expired ({str(exc)[:160]}). The P1 Manager "
                      "renews it within the hour; check the P1 channel for a message about it, "
                      "or run the 'Check smrtPhone login' workflow."})
    except ValueError as exc:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST,
                            content={"ok": False, "error": f"bad since/until: {exc}"})
    except Exception as exc:  # noqa: BLE001 - network or smrtPhone outage
        log.exception("call log read failed")
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            content={"ok": False, "error": "call_log", "detail": str(exc)[:200]})


@app.get("/hooks/{secret}/smrtphone-session")
def smrtphone_session_status(secret: str, request: Request) -> Any:
    """Whether the saved smrtPhone login still reads the call log.

    Asked hourly by the P1 Manager's login keeper (src/p1_manager/
    smrtphone_keeper.py), which renews the login when this says no.
    """
    if not _authorized(secret, request):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    from . import call_log

    return {"ok": True, **call_log.session_status()}


@app.post("/hooks/{secret}/smrtphone-session")
async def smrtphone_session(secret: str, request: Request) -> Any:
    """Save a smrtPhone browser login to SMRTPHONE_STATE_FILE on the volume.

    smrtPhone's call log and SMS log are only readable through the web app's
    session, and flyctl cannot reach this app from the office machine, so the
    login arrives here instead. The body is the JSON that
    src/call_coaching/smrtphone_login.py saves. Only smrtPhone cookies are
    kept. The reply never echoes a cookie; it says how many were saved and
    whether the call log reads with them.
    """
    if not _authorized(secret, request):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    try:
        state = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST,
                            content={"ok": False, "error": "body is not JSON"})
    from . import call_log

    try:
        saved = call_log.save_session(state)
    except ValueError as exc:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST,
                            content={"ok": False, "error": str(exc)})
    session_ok, detail = call_log.check_session()
    log.info("smrtPhone session uploaded: %s cookies, session_ok=%s", saved, session_ok)
    return {"ok": True, "cookies": saved, "session_ok": session_ok, "detail": detail}


CANARY_TEXT = ("Test from the CHS texting system. Reply to this text with anything "
               "to finish the test.")


@app.post("/hooks/{secret}/canary")
def canary(secret: str, request: Request, to: str) -> Any:
    """Send ONE test text to a team phone, through the real provider path.

    The go-live check: proves the provider key, the sending number and its
    A2P registration, and (once the team replies) the inbound webhook, while
    SMS_AGENT_DRY_RUN stays on for every seller. Only numbers listed in
    SMS_AGENT_CANARY_NUMBERS can receive it.
    """
    if not _authorized(secret, request):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    allowed = {store.clean_phone(n) for n in config.CANARY_NUMBERS}
    phone = store.clean_phone(to)
    if not phone or phone not in allowed:
        return Response(status_code=status.HTTP_403_FORBIDDEN,
                        content='{"ok": false, "error": "number not in SMS_AGENT_CANARY_NUMBERS"}',
                        media_type="application/json")
    from . import sender_pool, transport

    from_number = sender_pool.assign(phone, config.SENDER_NAME)
    if not from_number:
        return {"ok": False, "error": "no sending number in the pool"}
    result = transport.send(phone, CANARY_TEXT, from_number)
    if result.ok:
        store.ensure_conversation(phone, from_number=from_number)
        store.add_message(phone, "out", CANARY_TEXT, from_number=from_number,
                          sms_id=result.sms_id, author="canary")
        store.record_send(from_number, phone)
    log.info("canary to ...%s from %s: ok=%s %s", phone[-4:], from_number, result.ok, result.error)
    from . import escalate

    slack_ok = escalate._post(
        f":test_tube: Go-live test text {'sent' if result.ok else 'FAILED'} from {from_number} "
        f"to the team phone ending {phone[-4:]}."
        + ("" if result.ok else f" Error: {result.error}"))
    slack_detail = {"bot": escalate.bot_identity(), "channel": config.SLACK_CHANNEL,
                    "bot_error": escalate.LAST_BOT_ERROR}
    return {"ok": result.ok, "slack": slack_ok, "slack_detail": slack_detail, "from": from_number, "sms_id": result.sms_id,
            "error": result.error, "status_code": result.status_code}


@app.get("/health")
def health() -> dict:
    """Liveness only. Deliberately does NOT touch the database.

    It used to return full counters, which is a dozen COUNT queries. While the
    worker held a write transaction those reads waited on the lock, the check
    blew its timeout, and Fly restarted the machine. A liveness probe that can
    be blocked by the thing it is monitoring is worse than no probe: it turned
    a slow query into a reboot loop mid-campaign.
    """
    return {"ok": True, "phase": config.PHASE, "dry_run": config.DRY_RUN}


@app.get("/alive")
def alive():
    """Is the WORKER doing passes, not merely is the web process up?

    /health answers "did this container boot". That returned 200 the whole time
    the worker thread could have been wedged, so it cannot be the thing an
    external monitor watches. This reports the age of the last worker pass and
    returns 503 once it is stale, which is a condition a monitor can alert on
    without knowing anything about the app.
    """
    from fastapi.responses import JSONResponse

    from . import scheduler

    state = scheduler.liveness()
    return JSONResponse(state, status_code=200 if state.get("alive") else 503)


@app.get("/stats")
def stats() -> dict:
    """The counters. Separate from /health so a slow read can never restart us."""
    return {
        "ok": True,
        "phase": config.PHASE,
        "dry_run": config.DRY_RUN,
        "config_gaps": config.missing(),
        "stats": store.stats(),
    }
