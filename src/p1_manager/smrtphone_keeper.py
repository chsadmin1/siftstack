"""Keeps the CHS texting robot (chs-sms-agent) logged in to smrtPhone.

The SMS agent reads smrtPhone's call log through a saved web login: that is
how it pauses a seller's texts after a rep calls them, and how the daily call
coaching job gets its calls. The login runs out. This module, running inside
the P1 Manager, notices and renews it, and only asks a person when it has to.

Every SMRTPHONE_CHECK_MINUTES (60) during SMRTPHONE_CHECK_HOURS (6-20,
P1_TIMEZONE), the keeper asks the agent whether its login still reads the
call log (GET /hooks/{secret}/smrtphone-session). When it does not:

  * With SMRTPHONE_LOGIN_EMAIL + SMRTPHONE_LOGIN_PASSWORD set, it logs in
    itself (smrtphone_auth.py) and uploads the fresh login to the agent.
  * If smrtPhone asks for a code and SMRTPHONE_TOTP_SECRET (the authenticator
    key) is set, it makes the code itself. Nobody is asked anything.
  * Otherwise it posts in the P1 channel asking for the code, and the reply in
    that thread finishes the login (handle_code_reply, from slack_app).
  * Without a saved password it posts, once a day, how to renew it by hand.

The P1 Manager hosts this, not the agent, because it already owns the CHS
Slack app's Socket Mode listener. Slack delivers each event to only one
listening connection, so a second listener in the agent would steal replies.

Its state (the last good cookies, which also carry any "trust this device"
mark, and a login waiting on a code) is on the P1 Manager's volume. Nothing
here is ever written to Slack or a log except plain-words outcomes and the
`seen` page summaries (field names, never values).
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

import smrtphone_auth
import state

log = logging.getLogger("p1_smrtphone")

STATE_FILE = state.STATE_DIR / "smrtphone_keeper.json"
SETUP_DOC = "docs/setup/chs-coaching-and-kpi.md, step 1"
_LOCK = threading.RLock()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def check_minutes() -> int:
    return max(5, _int("SMRTPHONE_CHECK_MINUTES", 60))


def code_minutes() -> int:
    return max(2, _int("SMRTPHONE_CODE_MINUTES", 15))


def code_every_hours() -> int:
    """How long an unanswered code request is left before smrtPhone is asked again."""
    return max(1, _int("SMRTPHONE_CODE_EVERY_HOURS", 4))


def _hours() -> tuple[int, int]:
    m = re.match(r"^\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$", _env("SMRTPHONE_CHECK_HOURS", "6-20"))
    return (int(m.group(1)), int(m.group(2))) if m else (6, 20)


def _now() -> datetime:
    return datetime.now(ZoneInfo(_env("P1_TIMEZONE", "America/Boise")))


def in_hours(now: datetime | None = None) -> bool:
    start, end = _hours()
    return start <= (now or _now()).hour < end


def agent_ready() -> bool:
    return bool(_env("SMS_AGENT_URL") and _env("SMS_AGENT_WEBHOOK_SECRET"))


def credentials() -> tuple[str, str, str]:
    return (_env("SMRTPHONE_LOGIN_EMAIL"), os.environ.get("SMRTPHONE_LOGIN_PASSWORD", ""),
            _env("SMRTPHONE_TOTP_SECRET"))


def load() -> dict:
    return state._load(STATE_FILE, {})


def save(data: dict) -> None:
    state._save(STATE_FILE, data)


# ---------------------------------------------------------------- the agent

def _agent(method: str, payload: dict | None = None) -> dict:
    url = f"{_env('SMS_AGENT_URL').rstrip('/')}/hooks/{_env('SMS_AGENT_WEBHOOK_SECRET')}/smrtphone-session"
    last = ""
    for _ in range(3):  # Fly's edge often drops the first request after idle
        try:
            r = requests.request(method, url, json=payload, timeout=90)
            try:
                body = r.json()
            except ValueError:
                body = {}
            if r.status_code == 200:
                return body
            last = f"HTTP {r.status_code} {body.get('error') or ''}".strip()
            if r.status_code in (400, 404):
                break
        except requests.RequestException as exc:
            last = exc.__class__.__name__
        time.sleep(3)
    raise RuntimeError(f"the SMS agent did not answer ({last})")


def agent_status() -> dict:
    return _agent("GET")


def upload(login_state: dict) -> dict:
    return _agent("POST", login_state)


# ---------------------------------------------------------------- Slack text

def _impact() -> str:
    return ("Until it is, texts won't pause when a rep calls a seller, "
            "and call coaching can't see yesterday's calls.")


def _alert(data: dict, post, key: str, text: str) -> None:
    """Post a problem at most once a day per kind of problem."""
    stamp = f"{key}|{_now().date().isoformat()}"
    if data.get("last_alert") == stamp:
        return
    data["last_alert"] = stamp
    save(data)
    post(text)


# ---------------------------------------------------------------- the check

def run_check(post, force: bool = False) -> dict:
    """One check, and a renewal when it is needed (or forced). Returns what happened."""
    with _LOCK:
        data = load()
        if not agent_ready():
            return {"status": "not_configured", "detail": "SMS_AGENT_URL and SMS_AGENT_WEBHOOK_SECRET are needed"}

        if not force:
            try:
                status = agent_status()
            except RuntimeError as exc:
                log.warning("smrtPhone keeper: %s", exc)
                return {"status": "agent_unreachable", "detail": str(exc)}
            if status.get("session_ok"):
                if data.get("broken_since"):
                    data.pop("broken_since", None)
                    save(data)
                return {"status": "ok", "detail": status.get("detail", "")}
            data.setdefault("broken_since", _now().isoformat(timespec="minutes"))
            save(data)

        pending = data.get("pending")
        if pending and time.time() - pending.get("created", 0) < code_minutes() * 60 and not force:
            return {"status": "waiting_for_code"}

        email, password, totp_secret = credentials()
        if not (email and password):
            _alert(data, post, "no_credentials",
                   ":warning: *The texting robot's smrtPhone login has run out.* " + _impact()
                   + f" The robot has no smrtPhone password saved, so someone has to renew it by hand ({SETUP_DOC}),"
                   " or add the SMRTPHONE_LOGIN_EMAIL and SMRTPHONE_LOGIN_PASSWORD secrets in GitHub so it can do"
                   " this itself.")
            return {"status": "expired_no_credentials"}

        if not force and time.time() - data.get("last_attempt", 0) < 50 * 60:
            return {"status": "backoff"}
        # Logging in is what makes smrtPhone text a code, so an unanswered
        # request is not repeated every hour. "new code" in the thread, or a
        # forced run, asks again straight away.
        if (not force and data.get("needs_code")
                and time.time() - data.get("last_code_ask", 0) < code_every_hours() * 3600):
            return {"status": "waiting_for_code"}
        data["last_attempt"] = time.time()
        save(data)

        try:
            result = smrtphone_auth.login(email, password, totp_secret, cookies=data.get("cookies"))
        except smrtphone_auth.LoginError as exc:
            pass_through = exc
        except Exception as exc:  # noqa: BLE001 - a bug here must become a message, not a crash
            log.exception("smrtPhone keeper: unexpected login error")
            pass_through = smrtphone_auth.LoginError("unexpected", f"something unexpected went wrong"
                                                                   f" ({exc.__class__.__name__})")
        else:
            pass_through = None
        if pass_through is not None:
            exc = pass_through
            data["last_error"] = exc.plain
            save(data)
            hint = (" Check that the SMRTPHONE_LOGIN_PASSWORD secret in GitHub still matches the smrtPhone"
                    " password." if exc.stage == "password" else "")
            if exc.stage == "code" and totp_secret:
                hint = (" The authenticator key may be wrong; check the SMRTPHONE_TOTP_SECRET secret in GitHub.")
            if exc.stage == "key":
                hint = " Then run Actions > \"Check smrtPhone login\" again."
            _alert(data, post, f"failed:{exc.stage}",
                   f":warning: *The texting robot couldn't log back in to smrtPhone:* {exc.plain}. " + _impact()
                   + hint + f" To renew it by hand, see {SETUP_DOC}.")
            log.warning("smrtPhone keeper: login failed at %s: %s | %s", exc.stage, exc.plain, exc.seen)
            return {"status": "failed", "stage": exc.stage, "detail": exc.plain, "seen": exc.seen}

        if result.status == "need_code":
            ts = post(f":key: *smrtPhone needs a login code for the texting robot.* smrtPhone just sent a code for"
                      f" {email}, by text message or in the authenticator app. Reply in this thread with just the"
                      f" code, like `123456`, within {code_minutes()} minutes.")
            data["pending"] = result.pending
            data["pending_ts"] = ts or ""
            data["needs_code"] = not totp_secret
            data["last_code_ask"] = time.time()
            save(data)
            if ts:
                state.save_pending_ask(ts, {"kind": "smrtphone_code", "created": time.time()})
            return {"status": "waiting_for_code", "seen": result.seen}

        return _finish(data, result, post)


def _finish(data: dict, result, say) -> dict:
    try:
        up = upload(result.state)
    except RuntimeError as exc:
        _alert(data, say, "upload", f":warning: The robot logged in to smrtPhone but couldn't hand the login to the"
                                    f" texting agent: {exc}. It will try again within the hour.")
        return {"status": "upload_failed", "detail": str(exc)}
    if not up.get("session_ok"):
        _alert(data, say, "upload_rejected", ":warning: The robot logged in to smrtPhone, but the texting agent"
                                             " still can't read the call log with it. " + _impact()
               + f" To renew it by hand, see {SETUP_DOC}.")
        return {"status": "upload_rejected", "detail": up.get("detail", "")}

    data["cookies"] = result.state["cookies"]
    for key in ("pending", "pending_ts", "broken_since", "last_error", "last_alert", "needs_code",
                "last_code_ask"):
        data.pop(key, None)
    data["last_ok"] = _now().isoformat(timespec="minutes")
    save(data)
    how = {"authenticator": " It made the code itself from the authenticator key, so nobody had to do anything.",
           "stored": "", "password": ""}.get(result.method, "")
    if result.method != "stored":
        say(":white_check_mark: *The texting robot is logged in to smrtPhone again.*" + how)
    return {"status": "renewed", "method": result.method, "seen": result.seen}


# ---------------------------------------------------------------- the Slack reply

_NEW_CODE = re.compile(r"\b(new code|resend|again|retry|another)\b", re.I)


def handle_code_reply(thread_ts: str, text: str, pending_ask: dict, reply, post) -> None:
    """A reply in a "smrtPhone needs a login code" thread."""
    with _LOCK:
        data = load()
        if data.get("pending_ts") != thread_ts:
            state.pop_pending_ask(thread_ts)
            reply("This code request is closed. The robot will post a new one if it needs another code.")
            return

        if _NEW_CODE.search(text or ""):
            data.pop("pending", None)
            data["last_attempt"] = 0
            data["last_code_ask"] = 0
            save(data)
            state.pop_pending_ask(thread_ts)
            reply("OK, asking smrtPhone for a new code. Watch for a new message in this channel.")
            run_check(post, force=True)
            return

        pending = data.get("pending")
        if not pending or time.time() - pending.get("created", 0) > code_minutes() * 60:
            reply(f"That code request timed out after {code_minutes()} minutes. Reply `new code` to get a fresh one.")
            return

        m = re.search(r"\d{6,8}", re.sub(r"[\s-]", "", text or ""))
        if not m:
            reply("Reply with just the code from smrtPhone, like `123456`.")
            return

        try:
            result = smrtphone_auth.submit_code(pending, m.group(0))
        except smrtphone_auth.LoginError as exc:
            log.warning("smrtPhone keeper: code failed: %s | %s", exc.plain, exc.seen)
            reply(f":x: {exc.plain}. Check the code and reply again, or reply `new code` for a fresh one.")
            return

        outcome = _finish(data, result, reply)
        if outcome["status"] == "renewed":
            state.pop_pending_ask(thread_ts)


# ---------------------------------------------------------------- the watcher

def watch_forever(post) -> None:
    time.sleep(120)  # let the app and the agent settle after a deploy
    while True:
        try:
            if in_hours():
                outcome = run_check(post)
                if outcome.get("status") not in ("ok", "backoff", "waiting_for_code"):
                    log.info("smrtPhone keeper: %s", outcome)
        except Exception:  # noqa: BLE001 - a bad pass must never stop the watcher
            log.exception("smrtPhone keeper pass failed")
        time.sleep(check_minutes() * 60)


def start_watcher(post) -> threading.Thread:
    t = threading.Thread(target=watch_forever, args=(post,), daemon=True, name="p1-smrtphone-keeper")
    t.start()
    return t
