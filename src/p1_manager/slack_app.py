"""The weekly job's "mouth and ears": posts the new-P1-records question to
Slack and listens for a threaded reply naming a SiftLine board + phase.

Uses FastAPI + uvicorn, matching src/sms_agent/receiver.py's stack exactly
(already a dependency here -- no reason to add slack_bolt/slack_sdk on top of
it). Talks to Slack's plain Web API over `requests` the same way
DataSiftClient talks to DataSift: no SDK, just HTTP calls this module owns
end to end.

SECURITY: every inbound request is verified against SLACK_SIGNING_SECRET
before anything in the body is trusted (HMAC over "v0:{timestamp}:{body}",
Slack's own documented scheme) -- an unverified POST to this endpoint must
never be treated as a real Slack event.

THE ASK-AND-WAIT FLOW:
  1. weekly.py finds new records on a P1 preset and calls post_weekly_ask(),
     which posts one Slack message and returns its `ts`. weekly.py stores
     {ts: {preset_name, property_uuids}} via state.save_pending_ask().
  2. A human replies IN THAT THREAD with a board + phase, e.g.
     "Acquisitions / Make Offer".
  3. Slack POSTs a `message` event with `thread_ts` == the original ts.
     handle_event() looks up the pending ask, resolves the reply text
     against the account's REAL board/column names (never guesses one),
     creates a SiftLine card for every property_uuid in the batch, and
     replies in-thread confirming what happened.
  4. No reply, no timeout-driven action -- silence means wait, this touches
     a live pipeline (see datasift_client.py's docstring on the 2026-08-11
     incident for why writes here stay conservative).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
import time
from pathlib import Path

import requests
from fastapi import BackgroundTasks, FastAPI, Header, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import state  # noqa: E402
from datasift_client import DataSiftClient  # noqa: E402

log = logging.getLogger("p1_slack")

SLACK_API = "https://slack.com/api"
MAX_SAMPLE_ADDRESSES = 8


def _env(name: str) -> str:
    v = os.environ.get(name, "")
    if not v:
        raise RuntimeError(f"{name} is not set")
    return v


def _headers() -> dict:
    return {"Authorization": "Bearer " + _env("SLACK_BOT_TOKEN"),
            "Content-Type": "application/json; charset=utf-8"}


def _call(method: str, payload: dict) -> dict:
    r = requests.post(f"{SLACK_API}/{method}", headers=_headers(), json=payload, timeout=30)
    data = r.json()
    if not data.get("ok"):
        log.warning("Slack API %s failed: %s", method, data.get("error"))
    return data


# ---- outbound: the weekly ask + the monthly report ----

def post_weekly_ask(preset_name: str, rows: list[dict]) -> str | None:
    """Posts the new-records summary + the board/phase question. Returns the
    message ts (used as the thread key), or None on failure."""
    addresses = [r.get("address") for r in rows[:MAX_SAMPLE_ADDRESSES] if r.get("address")]
    more = len(rows) - len(addresses)
    lines = [
        f":inbox_tray: *{len(rows)} new record(s)* on *{preset_name}*",
        *[f"  • {a}" for a in addresses],
    ]
    if more > 0:
        lines.append(f"  ...and {more} more")
    lines.append("\nShould these go onto a *SiftLine board + phase*? "
                 "If yes, reply *in this thread* with the board and phase, "
                 "e.g. `Acquisitions / Make Offer`. No reply = no action.")
    resp = _call("chat.postMessage", {
        "channel": _env("SLACK_CHANNEL_ID"),
        "text": "\n".join(lines),
        "unfurl_links": False,
    })
    return resp.get("ts") if resp.get("ok") else None


def post_monthly_report(report_text: str, csv_path: str | None = None) -> None:
    channel = _env("SLACK_CHANNEL_ID")
    # Slack truncates very long messages; keep the headline in chat and let
    # the CSV carry the detail, same division of labor as the weekly digest.
    _call("chat.postMessage", {"channel": channel, "text": report_text[:3800]})
    if csv_path and os.path.exists(csv_path):
        _upload_file(channel, csv_path)


def _upload_file(channel: str, path: str) -> None:
    """Slack's modern 3-step upload (getUploadURLExternal -> PUT -> complete).

    completeUploadExternal's `channel_id` param (and the alternate `channels`
    name) were both verified live 2026-09-23 to return ok:true but leave the
    file's own `channels` list empty -- the file never actually lands as an
    attachment in the channel either way, a real Slack API quirk, not a typo
    here. The reliable fix, also verified live: complete the upload with NO
    channel param, then post the file's own `permalink` as a normal message.
    Less pretty than a native attachment, but it actually shows up."""
    size = os.path.getsize(path)
    filename = os.path.basename(path)
    # getUploadURLExternal takes query params, not a JSON body -- a plain GET.
    resp = requests.get(f"{SLACK_API}/files.getUploadURLExternal",
                        headers={"Authorization": "Bearer " + _env("SLACK_BOT_TOKEN")},
                        params={"filename": filename, "length": size}, timeout=30)
    step1 = resp.json()
    if not step1.get("ok"):
        log.warning("files.getUploadURLExternal failed: %s", step1.get("error"))
        return
    with open(path, "rb") as f:
        put_resp = requests.put(step1["upload_url"], data=f, timeout=120)
    if put_resp.status_code >= 300:
        log.warning("CSV upload PUT failed: HTTP %d", put_resp.status_code)
        return
    complete = _call("files.completeUploadExternal",
                     {"files": [{"id": step1["file_id"], "title": filename}]})
    files = complete.get("files") or []
    permalink = files[0].get("permalink") if files else None
    if permalink:
        _call("chat.postMessage", {"channel": channel, "text": f"Full CSV export: {permalink}"})
    else:
        log.warning("files.completeUploadExternal returned no permalink: %s", complete)


# ---- inbound: verify, parse the reply, act ----

def verify_signature(body: bytes, timestamp: str, signature: str) -> bool:
    if abs(time.time() - float(timestamp)) > 60 * 5:
        return False  # replay-attack guard, per Slack's own documented scheme
    secret = _env("SLACK_SIGNING_SECRET").encode()
    basestring = b"v0:" + timestamp.encode() + b":" + body
    digest = "v0=" + hmac.new(secret, basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def resolve_board_phase(client: DataSiftClient, text: str) -> tuple[dict, dict] | None:
    """Match free text like 'Acquisitions / Make Offer' or 'acquisitions make
    offer' against REAL board + column names on the account. Never guesses a
    board/column that doesn't exist -- returns None on no confident match."""
    boards = client.boards()
    t = text.lower()
    for board in boards:
        board_title = (board.get("title") or "").lower()
        if board_title and board_title not in t:
            continue
        columns = client.board_columns(board["uuid"])
        for col in columns:
            col_title = (col.get("title") or "").lower()
            if col_title and col_title in t:
                return board, col
    # No board name in the text (or it didn't match) -- try phase name alone
    # against every board's columns, but only act on an UNAMBIGUOUS match.
    hits = []
    for board in boards:
        for col in client.board_columns(board["uuid"]):
            col_title = (col.get("title") or "").lower()
            if col_title and col_title in t:
                hits.append((board, col))
    return hits[0] if len(hits) == 1 else None


def handle_reply(thread_ts: str, text: str, channel: str) -> None:
    """Deliberately supports MULTIPLE replies to the same weekly-ask thread,
    each pushing the batch to a different board+phase (Dan, 2026-09-25: 'I'm
    okay if leads go to more than one board'). The pending ask is never
    deleted on a successful match -- only when it's genuinely untracked to
    begin with -- so the thread stays repliable indefinitely. `resolved_targets`
    guards against a repeat reply to the SAME board+column silently double-
    creating cards for every property in the batch."""
    pending = state.load_pending_asks().get(thread_ts)
    if not pending:
        return  # a reply to something we're not tracking

    client = DataSiftClient.from_env()
    match = resolve_board_phase(client, text)
    if not match:
        _call("chat.postMessage", {
            "channel": channel, "thread_ts": thread_ts,
            "text": ":warning: Couldn't match that to a real board + phase on "
                    "the account -- nothing was moved. Reply again with the "
                    "exact board and phase name."})
        return

    board, column = match
    resolved = pending.setdefault("resolved_targets", [])
    target_key = f"{board['uuid']}/{column['uuid']}"
    if target_key in resolved:
        _call("chat.postMessage", {
            "channel": channel, "thread_ts": thread_ts,
            "text": f":information_source: Already added to *{board.get('title')} / "
                    f"{column.get('title')}* from an earlier reply -- skipped, no duplicates."})
        return

    created, failed = 0, 0
    for uuid in pending["property_uuids"]:
        try:
            client.create_siftline_card(column["uuid"], uuid)
            created += 1
        except Exception:  # noqa: BLE001
            failed += 1
    resolved.append(target_key)
    state.save_pending_ask(thread_ts, pending)  # stays open for further replies
    summary = (f":white_check_mark: Added {created} card(s) to "
              f"*{board.get('title')} / {column.get('title')}*")
    if failed:
        summary += f" ({failed} failed)"
    if len(resolved) > 1:
        summary += f"\n(now on {len(resolved)} boards total from this batch)"
    _call("chat.postMessage", {"channel": channel, "thread_ts": thread_ts, "text": summary})


app = FastAPI(title="P1 List Manager", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _startup() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("P1 List Manager receiver up")
    if os.environ.get("P1_INLINE_SCHEDULER", "1").strip() in ("1", "true", "yes", "on"):
        _start_inline_scheduler()


def _start_inline_scheduler() -> None:
    """Run the weekly/monthly scheduler loop in a thread inside this same
    process -- same reasoning as sms_agent/receiver.py's inline worker: the
    state file lives on one Fly volume attached to one machine, so splitting
    the HTTP receiver and the scheduler across two machines just adds a
    coordination problem for no benefit."""
    import threading

    import schedule

    thread = threading.Thread(target=schedule.main, args=([],), daemon=True,
                              name="p1-manager-scheduler")
    thread.start()
    log.info("inline scheduler started")


@app.post("/slack/events")
async def slack_events(request: Request, x_slack_signature: str = Header(None),
                       x_slack_request_timestamp: str = Header(None)):
    body = await request.body()
    if not (x_slack_signature and x_slack_request_timestamp
            and verify_signature(body, x_slack_request_timestamp, x_slack_signature)):
        return JSONResponse({"error": "bad signature"}, status_code=401)

    payload = await request.json()

    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge")})

    if payload.get("type") == "event_callback":
        event = payload.get("event") or {}
        if (event.get("type") == "message" and not event.get("bot_id")
                and event.get("thread_ts")):
            handle_reply(event["thread_ts"], event.get("text", ""), event.get("channel", ""))

    return JSONResponse({"ok": True})


@app.post("/trigger/{job}")
async def trigger(job: str, secret: str, background_tasks: BackgroundTasks,
                  only: str = "", commit: bool = True):
    """Manually fire a job INSIDE the deployed container, so its state (the
    weekly cursor, pending Slack asks) lands on the real /data volume instead
    of a local machine's throwaway state -- the gap that made an early manual
    test's Slack thread un-repliable (the ask was posted by a local run, so
    this app's own pending-asks file never knew about it). Gated by
    P1_TRIGGER_SECRET (a Fly secret, never in this source) rather than being
    open -- same shape as the SMS agent's secret-in-path webhook routes.

    POST /trigger/weekly?secret=...&only=Preset One|Preset Two&commit=true
    POST /trigger/monthly?secret=...&commit=false

    `only` is PIPE-separated, not comma-separated -- every real preset name
    in this account contains a comma ("... County, MO"), so splitting on
    commas silently shredded names and matched zero presets (caught live
    2026-09-23 testing this exact endpoint: "Scanning 0 P1 presets").
    """
    expected = os.environ.get("P1_TRIGGER_SECRET", "")
    if not expected or secret != expected:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if job not in ("weekly", "monthly"):
        return JSONResponse({"error": "job must be 'weekly' or 'monthly'"}, status_code=400)

    import schedule

    def _run():
        if job == "weekly":
            names = [n.strip() for n in only.split("|") if n.strip()] or None
            post_slack = post_weekly_ask if (commit and schedule._slack_ready()) else None
            import weekly
            # A scoped (only=...) run is a manual test -- it must never move
            # the shared cursor the real unscoped Saturday run relies on
            # (caught live 2026-09-23: a broken scoped attempt advanced the
            # cursor anyway and starved the very next retry's time window).
            weekly.run(commit=commit, post_slack=post_slack, only_presets=names,
                      update_cursor=(names is None))
        else:
            import monthly
            result = monthly.run(commit=commit)
            if commit and schedule._slack_ready():
                post_monthly_report(result["report_text"], result.get("repull", {}).get("csv_path"))

    background_tasks.add_task(_run)
    return JSONResponse({"ok": True, "job": job, "commit": commit,
                         "only": only or None, "note": "running in background, check logs"})


@app.post("/admin/reset-weekly-cursor")
async def reset_weekly_cursor(secret: str, days_ago: int = 7):
    """One-off fix for a corrupted weekly cursor (e.g. a scoped test run
    accidentally advancing it -- see the fix in the /trigger handler above).
    SSH access to this machine has been unreliable from the operator's
    Windows box, so this exists as an HTTP-reachable escape hatch."""
    expected = os.environ.get("P1_TRIGGER_SECRET", "")
    if not expected or secret != expected:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    import datetime as dt
    new_since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago))
    st = state.load_weekly_state()
    old = st.get("last_run_iso")
    st["last_run_iso"] = new_since.strftime("%Y-%m-%dT%H:%M:%SZ")
    state.save_weekly_state(st)
    return JSONResponse({"ok": True, "old": old, "new": st["last_run_iso"]})


@app.get("/healthz")
async def healthz():
    return {"ok": True}
