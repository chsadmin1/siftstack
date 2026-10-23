"""The texting robot's smrtPhone login keeper (P1 Manager), with zero network.

  * smrtphone_auth: authenticator codes match RFC 6238, and the login walks a
    fake smrtPhone (login form, code page, method choice, Cloudflare, wrong
    password, wrong code, a stored login that still works).
  * smrtphone_keeper: when it checks, renews, asks in Slack for a code, takes
    the reply, throttles unanswered asks, and posts each problem once a day.
  * slack_app: a reply in a code thread reaches the keeper, and
    /trigger/smrtphone answers with the outcome.
  * The cookies it produces are accepted by the SMS agent's upload.

Run: python tests/test_p1_smrtphone_keeper.py   (or pytest)
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp()
os.environ["SIFTSTACK_STATE_DIR"] = _tmp
os.environ["SMS_AGENT_DATA_DIR"] = _tmp
os.environ["SMS_AGENT_DB"] = str(Path(_tmp) / "agent.db")
os.environ["P1_INLINE_SCHEDULER"] = "0"
os.environ.pop("SLACK_APP_TOKEN", None)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "p1_manager"))

import requests  # noqa: E402
from requests.cookies import RequestsCookieJar  # noqa: E402

import smrtphone_auth as auth  # noqa: E402
import smrtphone_keeper as keeper  # noqa: E402
import state  # noqa: E402

SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of the RFC 6238 test key "12345678901234567890"

LOGIN_HTML = """<html><head><title>Login</title></head><body>
<form style="margin-bottom: 20px" action="/login_check" method="post">
  <input type="hidden" name="_csrf_token" value="csrf-1">
  <input type="text" name="_username"><input type="password" name="_password">
  <button id="loginBtn" type="submit">Log in</button>
</form><form action="/search" method="get"><input type="text" name="q"></form></body></html>"""

CODE_HTML = """<html><head><title>Two-factor</title></head><body>
<form class="formTopCenter2FA" action="/2fa_check" method="post">
  <p>Enter the code we sent by SMS</p>
  <input type="hidden" name="_csrf_token" value="csrf-2">
  <input type="text" name="_auth_code" autocomplete="one-time-code">
  <input type="checkbox" id="trust" name="_trusted" value="on"><label for="trust">Trust this device</label>
  <button type="submit">Verify</button>
</form>
<a href="/2fa?preferProvider=google">Use your authenticator app instead</a></body></html>"""

CHOICE_HTML = """<html><head><title>Choose</title></head><body>
<form action="/2fa/send" method="post"><input type="hidden" name="_csrf_token" value="c3">
  <button name="method" value="sms">Text me a code</button>
  <button name="method" value="google">Use authenticator app</button></form></body></html>"""

DASH_HTML = "<html><head><title>smrtPhone</title></head><body><form action='/s'><input name='q'></form></body></html>"
CLOUDFLARE_HTML = "<html><head><title>Just a moment...</title></head><body>cf-chl</body></html>"


class Resp:
    def __init__(self, url, text="", status=200, data=None):
        self.url, self.text, self.status_code, self._data = url, text, status, data

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


class FakeSmrtPhone:
    """Just enough of phone.smrt.studio to walk the login."""

    def __init__(self, two_factor=True, choice=False, cloudflare=False, code_sent="111111"):
        self.two_factor, self.choice, self.cloudflare = two_factor, choice, cloudflare
        self.code_sent = code_sent
        self.posts: list[tuple[str, dict]] = []
        self.logins = 0
        self.provider = "sms"

    def session(self, cookies=None):
        return FakeSession(self, cookies)


class FakeSession:
    def __init__(self, site, cookies=None):
        self.site, self.headers, self.cookies = site, {}, RequestsCookieJar()
        for c in cookies or []:
            self.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))

    def _state(self):
        return self.cookies.get("smrtPhone", domain="phone.smrt.studio")

    def get(self, url, timeout=0, params=None):
        site = self.site
        if site.cloudflare:
            return Resp(url, CLOUDFLARE_HTML, 403)
        if url.endswith("/login"):
            self.cookies.set("smrtPhone", "guest", domain="phone.smrt.studio", path="/")
            return Resp(url, LOGIN_HTML)
        if "preferProvider=google" in url:
            site.provider = "google"
            return Resp(auth.BASE + "/2fa", CODE_HTML.replace("by SMS", "in your authenticator app"))
        return Resp(url, DASH_HTML)

    def post(self, url, data=None, timeout=0, headers=None):
        site = self.site
        site.posts.append((url, dict(data or {})))
        if url.endswith("/logs/calls/filtered"):
            if self._state() == "logged-in":
                return Resp(url, data={"data": []})
            return Resp(auth.BASE + "/login", LOGIN_HTML)
        if url.endswith("/login_check"):
            if data.get("_password") != "right" or data.get("_csrf_token") != "csrf-1":
                return Resp(auth.BASE + "/login", LOGIN_HTML)
            site.logins += 1
            if not site.two_factor:
                self.cookies.set("smrtPhone", "logged-in", domain="phone.smrt.studio", path="/")
                return Resp(auth.BASE + "/dashboard", DASH_HTML)
            self.cookies.set("smrtPhone", "half", domain="phone.smrt.studio", path="/")
            if site.choice:
                return Resp(auth.BASE + "/2fa/choose", CHOICE_HTML)
            return Resp(auth.BASE + "/2fa", CODE_HTML)
        if url.endswith("/2fa/send"):
            site.provider = data.get("method", "sms")
            return Resp(auth.BASE + "/2fa", CODE_HTML)
        if url.endswith("/2fa_check"):
            want = auth.totp(SECRET) if site.provider == "google" else site.code_sent
            if self._state() == "half" and data.get("_auth_code") == want and data.get("_csrf_token") == "csrf-2":
                self.cookies.set("smrtPhone", "logged-in", domain="phone.smrt.studio", path="/")
                return Resp(auth.BASE + "/dashboard", DASH_HTML)
            return Resp(auth.BASE + "/2fa", CODE_HTML)
        return Resp(url, DASH_HTML)


def _use(site):
    auth._session = site.session
    return site


# ---------------------------------------------------------------- smrtphone_auth

def test_totp_matches_rfc_6238():
    assert auth.totp(SECRET, at=59) == "287082"
    assert auth.totp(SECRET, at=1111111109) == "081804"
    assert auth.totp(SECRET, at=1234567890) == "005924"
    spaced = " ".join(SECRET[i:i + 4] for i in range(0, len(SECRET), 4)).lower()
    assert auth.totp(spaced, at=59) == "287082"
    uri = f"otpauth://totp/smrtPhone:chs?secret={SECRET}&issuer=smrtPhone"
    assert auth.clean_secret(uri) == SECRET


def test_describe_names_fields_never_values():
    text = auth.describe(LOGIN_HTML, auth.BASE + "/login")
    assert "_csrf_token:hidden" in text and "_password:password" in text
    assert "csrf-1" not in text


def test_login_with_authenticator_key_needs_nobody():
    site = _use(FakeSmrtPhone())
    out = auth.login("chs@example.com", "right", SECRET)
    assert out.status == "ok" and out.method == "authenticator", out
    sent = dict(site.posts)[auth.BASE + "/2fa_check"]
    assert sent["_trusted"] == "on", "trust this device is ticked"
    assert any(c["name"] == "smrtPhone" and c["value"] == "logged-in" for c in out.state["cookies"])


def test_login_without_key_stops_for_a_code_then_finishes():
    site = _use(FakeSmrtPhone(code_sent="424242"))
    out = auth.login("chs@example.com", "right")
    assert out.status == "need_code" and out.pending["action"] == "/2fa_check", out
    try:
        auth.submit_code(out.pending, "000000")
        raise AssertionError("a wrong code must fail")
    except auth.LoginError as exc:
        assert exc.stage == "code"
    done = auth.submit_code(out.pending, "424242")
    assert done.status == "ok" and done.method == "text"
    assert site.logins == 1


def test_method_choice_picks_authenticator_or_text():
    site = _use(FakeSmrtPhone(choice=True))
    assert auth.login("chs@example.com", "right", SECRET).status == "ok"
    assert site.provider == "google"
    site = _use(FakeSmrtPhone(choice=True))
    assert auth.login("chs@example.com", "right").status == "need_code"
    assert site.provider == "sms"


def test_wrong_password_and_cloudflare_are_named():
    _use(FakeSmrtPhone())
    try:
        auth.login("chs@example.com", "wrong")
        raise AssertionError("wrong password must fail")
    except auth.LoginError as exc:
        assert exc.stage == "password" and "email or password" in exc.plain
    _use(FakeSmrtPhone(cloudflare=True))
    try:
        auth.login("chs@example.com", "right")
        raise AssertionError("cloudflare must fail")
    except auth.LoginError as exc:
        assert "Cloudflare" in exc.plain


def test_account_without_two_factor_and_stored_login():
    site = _use(FakeSmrtPhone(two_factor=False))
    out = auth.login("chs@example.com", "right")
    assert out.status == "ok" and out.method == "password"
    again = auth.login("chs@example.com", "right", cookies=out.state["cookies"])
    assert again.method == "stored" and site.logins == 1


def test_cookies_are_accepted_by_the_sms_agent():
    _use(FakeSmrtPhone(two_factor=False))
    out = auth.login("chs@example.com", "right")
    from src.sms_agent import call_log, config
    config.SMRTPHONE_STATE_FILE = str(Path(_tmp) / "agent_state.json")
    assert call_log.save_session(out.state) == 1


# ---------------------------------------------------------------- smrtphone_keeper

class Harness:
    def __init__(self, session_ok=False):
        self.posts, self.replies, self.uploads, self.logins = [], [], [], []
        self.session_ok = session_ok
        keeper.agent_status = lambda: {"session_ok": self.session_ok, "detail": "x"}
        keeper.upload = lambda st: (self.uploads.append(st), {"session_ok": True})[1]
        os.environ.update(SMS_AGENT_URL="https://agent.example", SMS_AGENT_WEBHOOK_SECRET="s3cret")
        for k in ("SMRTPHONE_LOGIN_EMAIL", "SMRTPHONE_LOGIN_PASSWORD", "SMRTPHONE_TOTP_SECRET"):
            os.environ.pop(k, None)
        if keeper.STATE_FILE.exists():
            keeper.STATE_FILE.unlink()
        if state.PENDING_ASKS_FILE.exists():
            state.PENDING_ASKS_FILE.unlink()

    def post(self, text):
        self.posts.append(text)
        return f"ts-{len(self.posts)}"

    def reply(self, text):
        self.replies.append(text)

    def creds(self, totp=""):
        os.environ.update(SMRTPHONE_LOGIN_EMAIL="chs@example.com", SMRTPHONE_LOGIN_PASSWORD="right")
        if totp:
            os.environ["SMRTPHONE_TOTP_SECRET"] = totp


def test_keeper_does_nothing_while_the_login_works():
    h = Harness(session_ok=True)
    assert keeper.run_check(h.post)["status"] == "ok"
    assert h.posts == [] and h.uploads == []


def test_keeper_without_password_posts_once_a_day():
    h = Harness()
    assert keeper.run_check(h.post)["status"] == "expired_no_credentials"
    assert keeper.run_check(h.post)["status"] == "expired_no_credentials"
    assert len(h.posts) == 1 and "run out" in h.posts[0]


def test_keeper_renews_with_authenticator_key():
    h = Harness()
    h.creds(totp=SECRET)
    _use(FakeSmrtPhone())
    out = keeper.run_check(h.post)
    assert out["status"] == "renewed" and out["method"] == "authenticator", out
    assert len(h.uploads) == 1 and "logged in to smrtPhone again" in h.posts[-1]
    assert keeper.load()["cookies"], "the good cookies are kept for next time"


def test_keeper_asks_for_a_code_and_the_reply_finishes():
    h = Harness()
    h.creds()
    _use(FakeSmrtPhone(code_sent="135790"))
    out = keeper.run_check(h.post)
    assert out["status"] == "waiting_for_code"
    assert "login code" in h.posts[0]
    asks = state.load_pending_asks()
    assert asks["ts-1"]["kind"] == "smrtphone_code"
    assert keeper.run_check(h.post)["status"] == "waiting_for_code", "no second login while waiting"

    keeper.handle_code_reply("ts-1", "the code is 135 790", asks["ts-1"], h.reply, h.post)
    assert len(h.uploads) == 1 and "logged in to smrtPhone again" in h.replies[-1], h.replies
    assert "ts-1" not in state.load_pending_asks()


def test_code_reply_without_digits_and_new_code():
    h = Harness()
    h.creds()
    site = _use(FakeSmrtPhone())
    keeper.run_check(h.post)
    keeper.handle_code_reply("ts-1", "hello?", {}, h.reply, h.post)
    assert "just the code" in h.replies[-1]
    keeper.handle_code_reply("ts-1", "new code please", {}, h.reply, h.post)
    assert site.logins == 2 and "login code" in h.posts[-1]
    keeper.handle_code_reply("ts-1", "123456", {}, h.reply, h.post)
    assert "closed" in h.replies[-1], "the old thread no longer takes codes"


def test_unanswered_code_is_not_asked_every_hour():
    h = Harness()
    h.creds()
    site = _use(FakeSmrtPhone())
    keeper.run_check(h.post)
    data = keeper.load()
    data["pending"]["created"] = time.time() - 3600  # the 15 minutes ran out
    data["last_attempt"] = 0
    keeper.save(data)
    assert keeper.run_check(h.post)["status"] == "waiting_for_code"
    assert site.logins == 1 and len(h.posts) == 1


def test_login_failure_posts_once_a_day():
    h = Harness()
    os.environ.update(SMRTPHONE_LOGIN_EMAIL="chs@example.com", SMRTPHONE_LOGIN_PASSWORD="wrong")
    _use(FakeSmrtPhone())
    assert keeper.run_check(h.post)["stage"] == "password"
    keeper.save({**keeper.load(), "last_attempt": 0})
    keeper.run_check(h.post)
    assert len(h.posts) == 1 and "SMRTPHONE_LOGIN_PASSWORD" in h.posts[0]


# ---------------------------------------------------------------- slack_app

def test_slack_reply_in_a_code_thread_reaches_the_keeper():
    import slack_app
    h = Harness()
    seen = []
    keeper_reply = keeper.handle_code_reply
    keeper.handle_code_reply = lambda ts, text, pending, reply, post: seen.append((ts, text, pending["kind"]))
    sent = []
    old_call = slack_app._call
    slack_app._call = lambda method, payload: sent.append(payload) or {"ok": True}
    try:
        state.save_pending_ask("ts-9", {"kind": "smrtphone_code", "created": time.time()})
        slack_app.handle_reply("ts-9", "246810", "C123")
    finally:
        keeper.handle_code_reply = keeper_reply
        slack_app._call = old_call
    assert seen == [("ts-9", "246810", "smrtphone_code")], seen


def test_trigger_reports_the_outcome():
    from fastapi.testclient import TestClient

    import slack_app
    h = Harness(session_ok=True)
    os.environ["P1_TRIGGER_SECRET"] = "trig"
    with TestClient(slack_app.app) as client:
        assert client.post("/trigger/smrtphone?secret=nope").status_code == 403
        body = client.post("/trigger/smrtphone?secret=trig").json()
    assert body["job"] == "smrtphone" and body["status"] == "ok", body


def test_a_bad_authenticator_key_is_named_before_any_login():
    assert auth.key_problem(SECRET) == ""
    assert "one-time code" in auth.key_problem("123456")
    assert "0, 1, 8 or 9" in auth.key_problem("ABCD1234EFGH5678IJKL")
    assert "symbols" in auth.key_problem("ABCD-EFGH_IJKL+MNOP")
    site = _use(FakeSmrtPhone())
    try:
        auth.login("chs@example.com", "right", "123456")
        raise AssertionError("a bad key must fail")
    except auth.LoginError as exc:
        assert exc.stage == "key" and "SMRTPHONE_TOTP_SECRET" in exc.plain
        assert "123456" not in exc.plain, "the key is never repeated"
    assert site.posts == [], "nothing was sent to smrtPhone"
    h = Harness()
    h.creds(totp="98765432")
    out = keeper.run_check(h.post)
    assert out["status"] == "failed" and out["stage"] == "key", out
    assert "SMRTPHONE_TOTP_SECRET" in h.posts[0] and "98765432" not in h.posts[0]


def test_an_unexpected_error_becomes_a_message():
    h = Harness()
    h.creds()
    real = auth.login
    auth.login = lambda *a, **k: (_ for _ in ()).throw(KeyError("boom"))
    try:
        out = keeper.run_check(h.post)
    finally:
        auth.login = real
    assert out["status"] == "failed" and out["stage"] == "unexpected", out
    assert "unexpected" in h.posts[0]


def test_trigger_never_answers_500():
    from fastapi.testclient import TestClient

    import slack_app
    os.environ["P1_TRIGGER_SECRET"] = "trig"
    real = keeper.run_check
    keeper.run_check = lambda post, force=False: (_ for _ in ()).throw(RuntimeError("x"))
    try:
        with TestClient(slack_app.app) as client:
            resp = client.post("/trigger/smrtphone?secret=trig")
    finally:
        keeper.run_check = real
    assert resp.status_code == 200 and resp.json()["status"] == "error", resp.text


def test_secrets_are_masked_in_access_logs():
    import logging

    import slack_app
    assert slack_app.redact("POST /trigger/smrtphone?secret=AbC-1_2&force=true") ==         "POST /trigger/smrtphone?secret=***&force=true"
    assert slack_app.redact("GET /hooks/Sek_ret-9/calls") == "GET /hooks/***/calls"
    rec = logging.LogRecord("uvicorn.access", 20, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "POST", "/trigger/weekly?secret=s3cr3t&commit=true", "1.1", 200), None)
    for f in logging.getLogger("uvicorn.access").filters:
        f.filter(rec)
    assert "s3cr3t" not in rec.getMessage() and "secret=***" in rec.getMessage()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} passed")
