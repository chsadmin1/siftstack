"""CHS call coaching on smrtPhone + the KPI digest bot, with zero network.

  * src/sms_agent/call_log.py turns smrtPhone call-log rows into the call
    dicts the coaching engine reads (recording URL, rep, contact), and
    call_takeover.py pauses a texting thread when a rep calls that seller.
  * src/call_coaching/chs_daily_audit.py samples, grades, writes rows and
    builds the Slack summary; every outside call is stubbed.
  * src/kpi_bot/kpi_slack_bot.py builds its Slack message from a fake sheet,
    including the per-rep section and the header-whitespace fix.

Run: python tests/test_chs_coaching_kpi.py   (or pytest)
"""
import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp()
os.environ["SMS_AGENT_DB"] = str(Path(_tmp) / "agent.db")
os.environ["SMS_AGENT_DATA_DIR"] = _tmp
# CHS texts and calls through smrtPhone (fly.chs-sms-agent.toml).
os.environ["SMS_AGENT_CALL_LOG_POLL"] = "1"
os.environ["SMS_AGENT_PHASE"] = "1"
os.environ["SMS_AGENT_NUMBERS"] = json.dumps({"Nick": ["+12085550100"]})
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "call_coaching"))
sys.path.insert(0, str(ROOT / "src" / "kpi_bot"))

from src.sms_agent import call_log, store  # noqa: E402


def _row(cid, created, to="+12085559999", secs=184, direction="outbound"):
    """One smrtPhone call-log row, in the shapes verified live 2026-08-28."""
    return {
        "id": cid,
        "user": {"id": 7, "name": "Nick"},
        "created_at": {"date": f"{created}.000000", "timezone_type": 3, "timezone": "UTC"},
        "direction": direction,
        "status": "completed",
        "disposition": "Correct Number",
        "from_num": {"fromNum": "+12085550100", "userName": "Nick - 1"},
        "to_num": {"toNum": to, "contactName": "Jane Seller"},
        "duration": str(secs),
        "podio_id": "https://app.reisift.io/records/properties/11111111-2222-3333-4444-555555555555",
        "recording_sid": {"hasRec": "https://rec.smrtphone.io/abc-123.mp3", "viewRoute": "/logs/calls/abc"},
    }


def test_call_log_from_smrtphone_rows():
    store.init()
    pages = {0: [_row("abc-123", "2026-10-06 18:47:15")]}
    calls = call_log.calls_between("2026-10-06T00:00:00Z", "2026-10-07T00:00:00Z",
                                   fetch_page=lambda n: pages.get(n, []))
    assert len(calls) == 1, calls
    c = calls[0]
    assert c["call_id"] == "abc-123"
    assert c["caller"] == "Nick", c["caller"]  # smrtPhone's own user name
    assert c["recording_url"] == "https://rec.smrtphone.io/abc-123.mp3"
    assert c["duration_seconds"] == 184
    assert c["created_at_utc"] == "2026-10-06 18:47:15"
    assert c["from_num"] == "+12085550100" and c["to_num"] == "+12085559999"
    assert c["contact_phone"] == "+12085559999"
    assert c["contact_name"] == "Jane Seller"
    assert c["disposition"] == "Correct Number"
    assert "11111111-2222-3333-4444-555555555555" in c["reisift_record_url"]


def test_smrtphone_call_pauses_text_thread():
    """A rep's real smrtPhone call stops the texting thread with that seller."""
    from src.sms_agent import call_takeover
    store.init()
    store.ensure_conversation("2085557777")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    orig = call_takeover.fetch_calls
    call_takeover.fetch_calls = lambda pages=2, per_page=200: [_row("call-777", now, to="+12085557777", secs=95)]
    try:
        out = call_takeover.run(pages=1, hours=6, apply=True)
    finally:
        call_takeover.fetch_calls = orig
    assert out.get("paused") == 1, out
    assert store.get_conversation("2085557777")["state"] == "paused"


def test_worker_sweeps_smrtphone_call_log():
    """Calls are in smrtPhone, so the worker reads its call log on the reconcile timer."""
    from src.sms_agent import config, worker
    assert config.CALL_LOG_POLL is True
    import src.sms_agent.call_takeover as ct
    import src.sms_agent.reconcile as rc
    called = []
    orig_ct, orig_rc = ct.run, rc.run
    ct.run = lambda **k: called.append(k) or {}
    rc.run = lambda **k: {}
    old_interval = config.RECONCILE_INTERVAL
    config.RECONCILE_INTERVAL = 1
    worker._last_reconcile = 0.0
    try:
        worker.run_once(with_reconcile=True)
    finally:
        ct.run, rc.run, config.RECONCILE_INTERVAL = orig_ct, orig_rc, old_interval
    assert called == [{"pages": 1, "hours": 6}], called


def test_expired_smrtphone_login_is_named_in_the_error():
    """The coaching job says the login expired, rather than 'could not reach'."""
    import io
    import urllib.error
    A = _audit_module()
    body = json.dumps({"ok": False, "error": "smrtphone_session",
                       "detail": "smrtPhone login is missing or expired. Run the 'Upload smrtPhone session' workflow."})
    attempts = []

    def fake_urlopen(req, timeout=0):
        attempts.append(req)
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(body.encode()))

    old = (A.urllib.request.urlopen, os.environ.get("SMS_AGENT_URL"), os.environ.get("SMS_AGENT_WEBHOOK_SECRET"))
    A.urllib.request.urlopen = fake_urlopen
    os.environ["SMS_AGENT_URL"], os.environ["SMS_AGENT_WEBHOOK_SECRET"] = "https://agent.example", "s3cret"
    try:
        A.fetch_calls(datetime.date(2026, 10, 6))
        raised = ""
    except RuntimeError as e:
        raised = str(e)
    finally:
        A.urllib.request.urlopen = old[0]
        for k, v in (("SMS_AGENT_URL", old[1]), ("SMS_AGENT_WEBHOOK_SECRET", old[2])):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert "Upload smrtPhone session" in raised, raised
    assert len(attempts) == 1, "an expired login is not retried"


def test_canary_only_texts_allowlisted_team_phones():
    from fastapi.testclient import TestClient

    from src.sms_agent import config, receiver, transport
    from src.sms_agent.smrtphone import SendResult
    sent = []
    old = (config.WEBHOOK_SECRET, config.CANARY_NUMBERS, transport.send, config.SENDER_NAME)
    config.WEBHOOK_SECRET, config.CANARY_NUMBERS, config.SENDER_NAME = "s3cret", ["+12085551234"], "Nick"
    transport.send = lambda to, body, frm="": sent.append((to, frm)) or SendResult(True, sms_id="sm1")
    from src.sms_agent import escalate
    old_post = escalate._post
    escalate._post = lambda text, blocks=None, program="": True
    old_ident = escalate.bot_identity
    escalate.bot_identity = lambda: {"ok": True, "team": "CHS"}
    try:
        c = TestClient(receiver.app)
        assert c.post("/hooks/wrong/canary?to=2085551234").status_code == 404
        assert c.post("/hooks/s3cret/canary?to=2085559999").status_code == 403  # a seller, say
        r = c.post("/hooks/s3cret/canary?to=(208) 555-1234")
        assert r.status_code == 200 and r.json()["ok"], r.text
    finally:
        config.WEBHOOK_SECRET, config.CANARY_NUMBERS, transport.send, config.SENDER_NAME = old
        escalate._post = old_post
        escalate.bot_identity = old_ident
    assert r.json()["slack"] is True and r.json()["slack_detail"]["bot"]["team"] == "CHS"
    assert sent == [("2085551234", "+12085550100")], sent  # from Nick's pool number
    assert store.get_conversation("2085551234")["from_number"] == "+12085550100"


def test_slack_uses_bot_token_and_channel_before_webhook():
    from src.sms_agent import config, escalate
    calls = []

    class R:
        def __init__(self, data): self._d = data; self.status_code = 200; self.text = ""
        def json(self): return self._d

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append((url, json.loads(data), headers.get("Authorization")))
        return R({"ok": True}) if "chat.postMessage" in url else R({})
    old = (config.SLACK_BOT_TOKEN, config.SLACK_CHANNEL, config.SLACK_WEBHOOK_URL, escalate.requests.post)
    config.SLACK_BOT_TOKEN, config.SLACK_CHANNEL = "xoxb-test", "C0C74TX29E2"
    config.SLACK_WEBHOOK_URL = "https://hooks.example/old"
    escalate.requests.post = fake_post
    try:
        assert escalate._post("hot lead") is True
    finally:
        config.SLACK_BOT_TOKEN, config.SLACK_CHANNEL, config.SLACK_WEBHOOK_URL, escalate.requests.post = old
    assert len(calls) == 1 and calls[0][0].endswith("chat.postMessage")
    assert calls[0][1]["channel"] == "C0C74TX29E2" and calls[0][2] == "Bearer xoxb-test"


def test_slack_bot_joins_channel_on_not_in_channel():
    from src.sms_agent import config, escalate
    seq = []

    class R:
        def __init__(self, d): self._d = d
        def json(self): return self._d
    joined = {"v": False}

    def fake_post(url, data=None, headers=None, timeout=None):
        m = url.rsplit("/", 1)[-1]; seq.append(m)
        if m == "conversations.join":
            joined["v"] = True
            return R({"ok": True})
        return R({"ok": True}) if joined["v"] else R({"ok": False, "error": "not_in_channel"})
    old = (config.SLACK_BOT_TOKEN, config.SLACK_CHANNEL, config.SLACK_WEBHOOK_URL, escalate.requests.post)
    config.SLACK_BOT_TOKEN, config.SLACK_CHANNEL, config.SLACK_WEBHOOK_URL = "xoxb-t", "C1", ""
    escalate.requests.post = fake_post
    try:
        assert escalate._post("hi") is True
    finally:
        config.SLACK_BOT_TOKEN, config.SLACK_CHANNEL, config.SLACK_WEBHOOK_URL, escalate.requests.post = old
    assert seq == ["chat.postMessage", "conversations.join", "chat.postMessage"], seq


def test_call_log_window_excludes_other_days():
    rows = [_row("late", "2026-10-08 12:00:00"), _row("early", "2026-10-05 12:00:00")]
    assert call_log.calls_between("2026-10-06T00:00:00Z", "2026-10-07T00:00:00Z",
                                  fetch_page=lambda n: rows if n == 0 else []) == []


def _audit_module():
    import chs_daily_audit as A
    return A


def test_qualifies_and_local_date():
    A = _audit_module()
    assert A.qualifies({"recording_url": "x", "duration_seconds": 61}, 60)
    assert not A.qualifies({"recording_url": "", "duration_seconds": 600}, 60)
    assert not A.qualifies({"recording_url": "x", "duration_seconds": 20}, 60)
    assert A.qualifies({"recording_url": "x", "duration_seconds": 20, "disposition": "Correct Number"}, 60)
    # 02:30 UTC Oct 7 is still Oct 6 in Boise
    assert A.local_date("2026-10-07 02:30:00") == datetime.date(2026, 10, 6)


def test_daily_audit_end_to_end_stubbed():
    A = _audit_module()
    day = datetime.date(2026, 10, 6)
    calls = [
        {**c, "call_id": f"id{i}", "caller": rep}
        for i, (rep, c) in enumerate([
            ("Nick", {"recording_url": "u", "duration_seconds": 200, "to_num": "+1", "contact_phone": "+12085559999"}),
            ("Nick", {"recording_url": "u", "duration_seconds": 30}),           # too short
            ("Amy", {"recording_url": "u", "duration_seconds": 90, "contact_name": "Bob"}),
            ("Amy", {"recording_url": "u", "duration_seconds": 95}),            # voicemail
        ])
    ]
    written, posted = [], []
    tr_dir = Path(_tmp) / "tr"
    tr_dir.mkdir(exist_ok=True)

    def fake_process(call):
        if call["call_id"] == "id3":
            return {"worth_grading": False, "call_type": "voicemail"}
        f = tr_dir / f"{call['call_id']}.md"
        f.write_text("AGENT: hi\nSELLER: hello", encoding="utf-8")
        return {"worth_grading": True, "pipeline": "cold_call", "transcript_file": str(f)}

    def fake_grade(text, skill, context=""):
        return {"report_type": "full", "score": 72, "grade_band": "Solid", "auto_fail": False,
                "auto_fail_reason": "", "call_summary": "Talked timeline.", "did_well": "Warm open.",
                "improve": "Ask about price.", "category_scores": [], "model": "m"}

    saved = {}
    patches = {
        (A, "fetch_calls"): lambda d: calls,
        (A.append_audit_row, "column_values"): lambda h: {"already"},
        (A.pull_calls, "download_recording"): lambda url, dest: (dest.write_bytes(b"0" * 5000), True)[1],
        (A.pull_calls, "OUT_DIR"): Path(_tmp),
        (A.pull_calls, "REC_DIR"): Path(_tmp) / "rec",
        (A, "OUT_DIR"): Path(_tmp),
        (A.transcribe, "TR_DIR"): tr_dir,
        (A.transcribe, "process"): fake_process,
        (A.grader, "grade"): fake_grade,
        (A.append_audit_row, "append"): lambda rows, notify=True: (written.extend(rows), {
            "success": True, "appended": len(rows), "range_url": "https://sheet"})[1],
        (A, "post_slack"): posted.append,
    }
    for (mod, name), val in patches.items():
        saved[(mod, name)] = getattr(mod, name)
        setattr(mod, name, val)
    argv = sys.argv
    try:
        sys.argv = ["x", "--date", day.isoformat()]
        assert A.main() == 0
    finally:
        sys.argv = argv
        for (mod, name), val in saved.items():
            setattr(mod, name, val)

    assert {r["Call ID"] for r in written} == {"id0", "id2"}, written
    assert all(r["Score"] == "72" and r["Rubric"] == "Cold call" for r in written)
    assert set(written[0]) <= set(A.HEADERS), set(written[0]) - set(A.HEADERS)
    assert len(posted) == 1 and "2 calls graded" in posted[0] and "Ask about price." in posted[0], posted


def test_slack_summary_empty_and_autofail():
    A = _audit_module()
    day = datetime.date(2026, 10, 6)
    assert "no calls to grade" in A.slack_summary(day, [], 5, 0, "")
    row = {h: "" for h in A.HEADERS}
    row.update({"Sales Rep": "Nick", "Rubric": "Cold call", "Score": "", "Grade": "FAIL",
                "Auto-fail": "YES", "Areas for improvement": "Honor the opt-out."})
    text = A.slack_summary(day, [row], 1, 0, "https://sheet")
    assert "AUTO-FAIL" in text and "https://sheet" in text


class FakeWS:
    def __init__(self, rows):
        self._rows = rows

    def get_all_values(self):
        return self._rows


class FakeSheet:
    def __init__(self, tabs):
        self.tabs = tabs

    def worksheet(self, name):
        import gspread
        if name not in self.tabs:
            raise gspread.exceptions.WorksheetNotFound(name)
        return FakeWS(self.tabs[name])


def test_kpi_bot_message():
    os.environ["KPI_REPS"] = "Nick, Amy, Sam"
    import importlib

    import kpi_slack_bot as K
    K = importlib.reload(K)
    today = K._now().date()
    d = today.strftime("%m/%d/%Y")
    sheet = FakeSheet({
        # trailing space in the header, blank extra columns, a blank row
        "First to Market": [
            ["Ninja Name ", "Date", "Hours", "Initial Dial", "FU 1 Dial", "Correct Initial", "New Lead", "", ""],
            ["Nick", d, "6", "120", "30", "14", "2", "", ""],
            ["", "", "", "", "", "", "", "", ""],
        ],
        "Lead Management": [
            ["Name", "Date", "Hours", "Dials Made", "Conversations", "New Leads Qualified"],
            ["Amy", d, "4", "40", "9", "3"],
        ],
        "Dialer": [["Date", "Caller", "Dials", "New Lead", "Talk Time (hour)"], [d, "Nick", "150", "2", "1.5"]],
        "Expenses": [["DATE", "Expense Type", "DESCRIPTION", "AMOUNT"], [d, "Software", "smrtPhone", "$95"]],
    })
    msg = K.build_slack_message(sheet)
    text = json.dumps(msg, ensure_ascii=False)
    assert "TODAY BY REP" in text
    assert "*Nick*, 6.0h: dials 150 (150)" in text, text[:1500]
    assert "correct #s 14 (14)" in text
    assert "*Amy*, 4.0h: dials 40 (40) | conversations 9 (9) | qualified 3 (3)" in text
    if today.weekday() < 5:
        assert "Not logged yet today:* Sam" in text
    assert "150 today" in text  # Dialer tab read, Talk Time column
    assert "1.5h today" in text
    assert "$95 today" in text  # AMOUNT column, not the old EXPENSE
    assert len(msg["blocks"]) <= 50


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} passed")
