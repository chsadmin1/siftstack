"""Log in to smrtPhone's web app without a browser, for the CHS SMS agent.

smrtPhone has no API for its call log or SMS log. The SMS agent reads both
through a saved web login (/data/smrtphone_state.json on chs-sms-agent), and
that login runs out. This module makes a fresh one with plain HTTP, so the
P1 Manager (smrtphone_keeper.py) can renew it without anyone running a
browser on the office machine.

What the login page looks like (checked 2026-10-23): a plain form that POSTs
to /login_check with _csrf_token, _username and _password (Symfony's form
login). After the password, accounts with two-factor login get a second page
asking for a code, sent by text or read from an authenticator app.

The code page could not be seen without the real login, so it is handled by
shape rather than by name: the first form on the page with a visible text
field becomes the code form. If the page offers a choice of method, the
authenticator option is picked when an authenticator key is configured,
otherwise the text-message option. Any "trust this device" checkbox is
ticked, so the next renewal may skip the code entirely. Every result carries
a `seen` summary of the page (form actions, field names, button labels, never
values) so a surprise can be diagnosed from one run.

    result = login(email, password, totp_secret="", cookies=None)
    result.status    "ok" | "need_code"
    result.state     Playwright-style {"cookies": [...], "origins": []} when ok
    result.pending   what submit_code() needs when need_code
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html as html_lib
import re
import struct
import time
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser

import requests

BASE = "https://phone.smrt.studio"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
TIMEOUT = 45

# Same DataTables contract as src/sms_agent/call_takeover.py.
_COLUMNS = ["id", "user", "user_id", "created_at", "direction", "status",
            "disposition", "from_num", "to_num", "price", "duration",
            "podio_id", "recording_sid", "sid", "call_agent_id"]

_CODE_NAME = re.compile(r"code|auth|otp|2fa|verif", re.I)
_SKIP_NAME = re.compile(r"csrf|_token$|^_target|remember|trusted", re.I)
_TRUST = re.compile(r"trust|remember", re.I)
_APP_WORDS = re.compile(r"authenticator|google|totp|\bapp\b", re.I)
_SMS_WORDS = re.compile(r"\bsms\b|text|phone|mobile", re.I)


class LoginError(RuntimeError):
    """A login step failed. `stage` says which, `plain` is fit for Slack."""

    def __init__(self, stage: str, plain: str, seen: str = ""):
        super().__init__(f"{stage}: {plain}")
        self.stage, self.plain, self.seen = stage, plain, seen


@dataclass
class Form:
    action: str = ""
    method: str = "post"
    inputs: list[dict] = field(default_factory=list)
    buttons: list[dict] = field(default_factory=list)
    selects: dict = field(default_factory=dict)
    text: str = ""

    def names(self) -> list[str]:
        return [i.get("name", "") for i in self.inputs if i.get("name")]

    def visible_fields(self) -> list[dict]:
        return [i for i in self.inputs
                if (i.get("type") or "text").lower() in ("text", "number", "tel", "password", "")
                and i.get("name")]


@dataclass
class Result:
    status: str
    state: dict | None = None
    pending: dict | None = None
    seen: str = ""
    method: str = ""


# ---------------------------------------------------------------- TOTP

def clean_secret(value: str) -> str:
    """An authenticator key as typed, or a whole otpauth:// link, -> base32."""
    value = (value or "").strip()
    if value.lower().startswith("otpauth://"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(value).query)
        value = (q.get("secret") or [""])[0]
    return re.sub(r"[\s-]", "", value).upper()


def key_problem(secret: str) -> str:
    """Why an authenticator key cannot work, in plain words; "" when it looks right.

    A key is base32: the letters A to Z and the digits 2 to 7, usually 16 to
    64 of them. Says what is wrong without ever repeating the key.
    """
    key = clean_secret(secret).rstrip("=")
    if not key:
        return "the authenticator key is empty"
    if key.isdigit():
        return (f"the saved authenticator key is {len(key)} digits, which looks like a one-time code "
                "or a recovery code rather than the key")
    bad = []
    if re.search(r"[0189]", key):
        bad.append("the digits 0, 1, 8 or 9")
    if re.search(r"[^A-Z0-9]", key):
        bad.append("symbols")
    if bad:
        return (f"the saved authenticator key has {len(key)} characters, including {' and '.join(bad)}. "
                "A key only uses the letters A to Z and the digits 2 to 7")
    if len(key) < 16:
        return f"the saved authenticator key has only {len(key)} characters; a key is usually 16 to 64"
    return ""


def totp(secret: str, at: float | None = None, digits: int = 6, period: int = 30) -> str:
    """RFC 6238 code from an authenticator key (SHA-1, the authenticator default)."""
    key_b32 = clean_secret(secret)
    key = base64.b32decode(key_b32 + "=" * (-len(key_b32) % 8), casefold=True)
    counter = int((time.time() if at is None else at) // period)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    number = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(number).zfill(digits)


# ---------------------------------------------------------------- HTML forms

class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list[Form] = []
        self._form: Form | None = None
        self._select: str | None = None
        self._button: dict | None = None
        self._label_for: str | None = None
        self.labels: dict[str, str] = {}
        self.links: list[dict] = []
        self._link: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "a" and a.get("href"):
            self._link = {"href": a["href"], "text": ""}
            self.links.append(self._link)
        if tag == "form":
            self._form = Form(action=a.get("action", ""), method=(a.get("method") or "post").lower())
            self.forms.append(self._form)
        elif self._form is None:
            return
        elif tag == "input":
            self._form.inputs.append({"name": a.get("name", ""), "type": (a.get("type") or "text").lower(),
                                      "value": a.get("value", ""), "checked": "checked" in a,
                                      "id": a.get("id", "")})
            if (a.get("type") or "").lower() == "submit" and a.get("name"):
                self._form.buttons.append({"name": a["name"], "value": a.get("value", ""),
                                           "label": a.get("value", "")})
        elif tag == "button":
            self._button = {"name": a.get("name", ""), "value": a.get("value", ""), "label": ""}
            self._form.buttons.append(self._button)
        elif tag == "select":
            self._select = a.get("name", "")
            self._form.selects[self._select] = []
        elif tag == "option" and self._select is not None:
            self._form.selects[self._select].append({"value": a.get("value", ""), "label": ""})
        elif tag == "label":
            self._label_for = a.get("for", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "select":
            self._select = None
        elif tag == "button":
            self._button = None
        elif tag == "label":
            self._label_for = None
        elif tag == "a":
            self._link = None

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text:
            return
        if self._form is not None:
            self._form.text = (self._form.text + " " + text).strip()
        if self._button is not None:
            self._button["label"] = (self._button["label"] + " " + text).strip()
        if self._select is not None and self._form is not None and self._form.selects.get(self._select):
            opt = self._form.selects[self._select][-1]
            opt["label"] = (opt["label"] + " " + text).strip()
        if self._link is not None:
            self._link["text"] = (self._link["text"] + " " + text).strip()
        if self._label_for:
            self.labels[self._label_for] = (self.labels.get(self._label_for, "") + " " + text).strip()


def _parse(html: str) -> _FormParser:
    p = _FormParser()
    p.feed(html or "")
    for form in p.forms:  # carry <label for=...> text onto radios and checkboxes
        for i in form.inputs:
            if i.get("id") and i["id"] in p.labels:
                i["label"] = p.labels[i["id"]]
    return p


def parse_forms(html: str) -> list[Form]:
    return _parse(html).forms


def parse_links(html: str) -> list[dict]:
    return _parse(html).links


def describe(html: str, url: str = "") -> str:
    """Field names, actions and button labels on a page. Never a value."""
    parts = [f"page {urllib.parse.urlparse(url).path or '?'}"]
    title = re.search(r"<title>(.*?)</title>", html or "", re.S | re.I)
    if title:
        parts.append("title " + " ".join(html_lib.unescape(title.group(1)).split())[:60])
    for f in parse_forms(html):
        fields = ",".join(f"{i.get('name') or '-'}:{i.get('type')}" for i in f.inputs)
        buttons = ",".join((b.get("label") or b.get("name") or "-")[:30] for b in f.buttons)
        selects = ",".join(f"{k}[{'/'.join(o['label'][:20] for o in v)}]" for k, v in f.selects.items())
        parts.append(f"form {f.action or '-'} fields {fields or '-'} buttons {buttons or '-'}"
                     + (f" selects {selects}" if selects else ""))
    return " | ".join(parts)[:1500]


def _payload(form: Form) -> dict:
    """Hidden fields and pre-ticked boxes, exactly as a browser would send them."""
    data: dict = {}
    for i in form.inputs:
        name, kind = i.get("name"), i.get("type")
        if not name or kind in ("submit", "button", "image", "file"):
            continue
        if kind in ("checkbox", "radio"):
            if i.get("checked"):
                data[name] = i.get("value") or "on"
            continue
        if kind == "hidden":
            data[name] = i.get("value", "")
    for name, options in form.selects.items():
        if name and options:
            data.setdefault(name, options[0]["value"])
    return data


def _cloudflare_block(resp: requests.Response) -> bool:
    body = resp.text[:5000].lower()
    return resp.status_code in (403, 429, 503) and (
        "cf-chl" in body or "just a moment" in body or "attention required" in body)


# ---------------------------------------------------------------- session

def _session(cookies: list[dict] | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA,
                      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                      "Accept-Language": "en-US,en;q=0.9"})
    for c in cookies or []:
        if c.get("name") and "smrt" in str(c.get("domain", "")):
            s.cookies.set(c["name"], c.get("value", ""), domain=c.get("domain"), path=c.get("path") or "/")
    return s


def export_cookies(s: requests.Session) -> list[dict]:
    """The session's smrtPhone cookies in Playwright's storage_state shape."""
    out = []
    for c in s.cookies:
        if "smrt" not in (c.domain or ""):
            continue
        rest = {k.lower(): v for k, v in getattr(c, "_rest", {}).items()}
        out.append({"name": c.name, "value": c.value or "", "domain": c.domain, "path": c.path or "/",
                    "expires": float(c.expires) if c.expires else -1, "httpOnly": "httponly" in rest,
                    "secure": bool(c.secure), "sameSite": "Lax"})
    return out


def check_call_log(s: requests.Session) -> bool:
    """True when the session can read one row of the call log (what the agent needs)."""
    form = {"draw": "1", "start": "0", "length": "1", "search[value]": "", "search[regex]": "false",
            "order[0][column]": "3", "order[0][dir]": "desc"}
    for i, col in enumerate(_COLUMNS):
        form.update({f"columns[{i}][data]": col, f"columns[{i}][name]": col,
                     f"columns[{i}][searchable]": "true", f"columns[{i}][orderable]": "true",
                     f"columns[{i}][search][value]": "", f"columns[{i}][search][regex]": "false"})
    try:
        r = s.post(BASE + "/logs/calls/filtered", data=form, timeout=TIMEOUT,
                   headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json",
                            "Referer": BASE + "/logs/calls", "Origin": BASE})
        return r.status_code == 200 and isinstance(r.json().get("data"), list)
    except (requests.RequestException, ValueError):
        return False


# ---------------------------------------------------------------- the flow

def _code_form(forms: list[Form]) -> Form | None:
    """The form on a code page. Only asked once the call log has refused the
    session, so a search box on a logged-in page can never be mistaken for it."""
    candidates = [f for f in forms if "login_check" not in f.action and
                  [i for i in f.visible_fields() if i["type"] != "password" and not _SKIP_NAME.search(i["name"])]]
    for f in candidates:
        if any(_CODE_NAME.search(i["name"]) for i in f.visible_fields()):
            return f
    return candidates[0] if len(candidates) == 1 else None


def _method_choice(forms: list[Form], want_app: bool) -> tuple[Form, dict] | None:
    """A page that asks HOW to send the code: pick a form, button, radio or select."""
    want, avoid = (_APP_WORDS, _SMS_WORDS) if want_app else (_SMS_WORDS, _APP_WORDS)
    for f in forms:
        if "login_check" in f.action:
            continue
        for name, options in f.selects.items():
            for o in options:
                if want.search(o["label"] + " " + o["value"]):
                    return f, {name: o["value"]}
        for i in f.inputs:
            if i.get("type") == "radio" and want.search(f"{i.get('label', '')} {i.get('value', '')}"):
                return f, {i["name"]: i.get("value") or "on"}
        for b in f.buttons:
            if want.search(f"{b.get('label', '')} {b.get('value', '')}") and not avoid.search(b.get("label", "")):
                return f, ({b["name"]: b.get("value", "")} if b.get("name") else {})
    for f in forms:  # one form per method, told apart by the words in it
        if "login_check" not in f.action and want.search(f.text) and not avoid.search(f.text):
            return f, {}
    return None


def _submit(s: requests.Session, base_url: str, form: Form, extra: dict) -> requests.Response:
    data = _payload(form)
    data.update(extra)
    url = urllib.parse.urljoin(base_url, form.action or base_url)
    if form.method == "get":
        return s.get(url, params=data, timeout=TIMEOUT)
    return s.post(url, data=data, timeout=TIMEOUT, headers={"Referer": base_url, "Origin": BASE})


def _code_extra(form: Form, code: str) -> dict:
    extra = {}
    for i in form.visible_fields():
        if i["type"] != "password" and not _SKIP_NAME.search(i["name"]):
            extra[i["name"]] = code
            break
    for i in form.inputs:  # "trust this device", so the next renewal can skip the code
        if i.get("type") == "checkbox" and _TRUST.search(f"{i.get('name', '')} {i.get('label', '')}"):
            extra[i["name"]] = i.get("value") or "on"
    return extra


def _after_code(s: requests.Session, resp: requests.Response, method: str) -> Result:
    seen = describe(resp.text, resp.url)
    if _cloudflare_block(resp):
        raise LoginError("code", "Cloudflare blocked the robot on the code page", seen)
    if not check_call_log(s):
        raise LoginError("code", "smrtPhone did not accept the code", seen)
    return Result("ok", state={"cookies": export_cookies(s), "origins": []}, seen=seen, method=method)


def _switch_to_app(s: requests.Session, resp: requests.Response) -> requests.Response:
    """On a code page that defaults to a text message, follow its
    "use the authenticator app instead" link when there is one."""
    page = _parse(resp.text)
    form = _code_form(page.forms)
    if form is not None and _APP_WORDS.search(form.text) and not _SMS_WORDS.search(form.text):
        return resp
    for link in page.links:
        if _APP_WORDS.search(f"{link['text']} {link['href']}"):
            return s.get(urllib.parse.urljoin(resp.url, link["href"]), timeout=TIMEOUT)
    return resp


def login(email: str, password: str, totp_secret: str = "", cookies: list[dict] | None = None) -> Result:
    """Log in, or confirm the stored cookies still work. Raises LoginError."""
    if totp_secret and key_problem(totp_secret):
        raise LoginError("key", key_problem(totp_secret) + ". Copy the key smrtPhone shows under the QR code"
                         " again and paste it into the SMRTPHONE_TOTP_SECRET secret in GitHub")
    s = _session(cookies)
    if cookies and check_call_log(s):
        return Result("ok", state={"cookies": export_cookies(s), "origins": []},
                      seen="stored login still valid", method="stored")
    try:
        r = s.get(BASE + "/login", timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise LoginError("reach", f"could not reach smrtPhone ({exc.__class__.__name__})") from None
    if _cloudflare_block(r):
        raise LoginError("reach", "Cloudflare blocked the robot before the login page", describe(r.text, r.url))

    forms = parse_forms(r.text)
    login_form = next((f for f in forms if "login_check" in f.action or
                       {"_username", "_password"} <= set(f.names())), None)
    if login_form is None:
        raise LoginError("login page", "the smrtPhone login page has changed", describe(r.text, r.url))
    user_field = next((n for n in login_form.names() if "user" in n.lower() or "email" in n.lower()), "_username")
    pass_field = next((i["name"] for i in login_form.inputs if i.get("type") == "password"), "_password")
    r = _submit(s, r.url, login_form, {user_field: email, pass_field: password})
    seen = describe(r.text, r.url)
    if _cloudflare_block(r):
        raise LoginError("password", "Cloudflare blocked the robot when it sent the password", seen)

    if check_call_log(s):
        return Result("ok", state={"cookies": export_cookies(s), "origins": []}, seen=seen, method="password")

    forms = parse_forms(r.text)
    if urllib.parse.urlparse(r.url).path.lower().startswith("/login") and any(
            "login_check" in f.action for f in forms):
        raise LoginError("password", "smrtPhone did not accept the email or password", seen)

    code_form = _code_form(forms)
    if code_form is None:
        choice = _method_choice(forms, want_app=bool(totp_secret))
        if choice is not None:
            r = _submit(s, r.url, choice[0], choice[1])
            seen = seen + " || " + describe(r.text, r.url)
            code_form = _code_form(parse_forms(r.text))
    elif totp_secret:
        switched = _switch_to_app(s, r)
        if switched is not r:
            r = switched
            seen = seen + " || " + describe(r.text, r.url)
            code_form = _code_form(parse_forms(r.text)) or code_form

    if code_form is None:
        raise LoginError("code page", "smrtPhone showed a page the robot does not recognise", seen)

    if totp_secret:
        r = _submit(s, r.url, code_form, _code_extra(code_form, totp(totp_secret)))
        return _after_code(s, r, "authenticator")

    return Result("need_code", seen=seen, method="text", pending={
        "cookies": export_cookies(s), "url": r.url, "action": code_form.action,
        "method": code_form.method, "inputs": code_form.inputs, "selects": code_form.selects,
        "created": time.time()})


def submit_code(pending: dict, code: str) -> Result:
    """Finish a login that stopped at the code page. Raises LoginError."""
    s = _session(pending.get("cookies"))
    form = Form(action=pending.get("action", ""), method=pending.get("method", "post"),
                inputs=pending.get("inputs") or [], selects=pending.get("selects") or {})
    r = _submit(s, pending.get("url") or BASE + "/login", form, _code_extra(form, code.strip()))
    return _after_code(s, r, "text")
