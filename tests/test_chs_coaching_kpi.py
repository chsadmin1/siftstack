"""CHS call coaching on Kixie + the KPI digest bot, with zero network.

  * src/sms_agent/call_log.py turns stored Kixie end-call webhooks into the
    call dicts the coaching engine reads (recording URL, rep, contact).
  * src/call_coaching/kixie_daily_audit.py samples, grades, writes rows and
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
# CHS texts through smrtPhone and calls through Kixie (fly.chs-sms-agent.toml).
os.environ["SMS_AGENT_PROVIDER"] = "smrtphone"
os.environ["SMS_AGENT_CALL_LOG_POLL"] = "0"
os.environ["SMS_AGENT_PHASE"] = "1"
os.environ["SMS_AGENT_NUMBERS"] = json.dumps({"Nick": ["+12085550100"]})
os.environ["SMS_AGENT_REP_NUMBERS"] = json.dumps({"+12085550100": "Nick"})
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "call_coaching"))
sys.path.insert(0, str(ROOT / "src" / "kpi_bot"))

from src.sms_agent import call_log, kixie, store  # noqa: E402

ENDCALL = {
    "hookevent": "endcall",
    "businessid": 1,
    "customernumber": "+12085559999",
    "businessnumber": "+12085550100",
    "data": {
        "callDetails": {
            "callid": "abc-123",
            "calldate": "2026-10-06T18:47:15.000Z",
            "fromnumber": "+12085550100",
            "tonumber": "+12085559999",
            "duration": 184,
            "calltype": "outgoing",
            "callstatus": "answered",
            "recordingurl": "https://calls.kixie.com/12345.mp3",
            "disposition": "not disposed",
            "fname": "Jane",
            "lname": "Seller",
            "calleridName": "Creative Home",
            "crmlink": "https://app.reisift.io/x",
        }
    },
}


def _ingest(raw):
    ev = kixie.normalize_webhook(raw)
    store.record_event("kixie", ev["event"], f"kixie:{ev['event']}:{ev.get('callId')}", ev)


def test_call_log_from_endcall_webhook():
    store.init()
    _ingest(ENDCALL)
    _ingest({"hookevent": "disposition", "data": {"callid": "abc-123", "disposition": "Correct Number"}})
    calls = call_log.calls_between("2000-01-01T00:00:00", "2100-01-01T00:00:00")
    assert len(calls) == 1, calls
    c = calls[0]
    assert c["call_id"] == "abc-123"
    assert c["caller"] == "Nick", c["caller"]  # rep map beats calleridName
    assert c["recording_url"] == "https://calls.kixie.com/12345.mp3"
    assert c["duration_seconds"] == 184
    assert c["created_at_utc"] == "2026-10-06 18:47:15"
    assert c["from_num"] == "+12085550100" and c["to_num"] == "+12085559999"
    assert c["contact_name"] == "Jane Seller"
    assert c["disposition"] == "Correct Number"  # the later disposition webhook wins


def test_kixie_call_pauses_smrtphone_text_thread():
    """The split setup's key guarantee: a rep's real Kixie call stops the
    smrtPhone texting thread with that seller."""
    from src.sms_agent import config, engine
    assert config.PROVIDER == "smrtphone"
    store.init()
    store.ensure_conversation("2085557777")
    raw = json.loads(json.dumps(ENDCALL))
    raw["customernumber"] = "+12085557777"
    raw["data"]["callDetails"].update(callid="call-777", tonumber="+12085557777", duration=95)
    out = engine.process("kixie", kixie.normalize_webhook(raw))
    assert out.get("action") == "call_takeover", out
    assert store.get_conversation("2085557777")["state"] == "paused"


def test_smrtphone_call_log_poll_is_off():
    """Calls never appear in smrtPhone's log here, so the worker must not poll it."""
    from src.sms_agent import config, worker
    assert config.CALL_LOG_POLL is False
    import src.sms_agent.call_takeover as ct
    called = []
    orig_ct, orig_rc = ct.run, None
    ct.run = lambda **k: called.append(k) or {}
    import src.sms_agent.reconcile as rc
    orig_rc = rc.run
    rc.run = lambda **k: {}
    old_interval = config.RECONCILE_INTERVAL
    config.RECONCILE_INTERVAL = 1
    worker._last_reconcile = 0.0
    try:
        worker.run_once(with_reconcile=True)
    finally:
        ct.run, rc.run, config.RECONCILE_INTERVAL = orig_ct, orig_rc, old_interval
    assert called == []


def test_call_outcome_variants_are_dispositions():
    base = {"data": {"callid": "368131317", "disposition": "Correct Number", "phone": "+12085559999",
                     "callDetails": {"callid": "uuid-1", "disposition": "Correct Number"}}}
    for hook in ("disposition", "calloutcome", None):
        raw = json.loads(json.dumps(base))
        if hook:
            raw["data"]["hookevent"] = hook
        ev = kixie.normalize_webhook(raw)
        assert ev["event"] == "kixie:disposition", (hook, ev["event"])
        assert ev["callId"] == "uuid-1"  # the UUID that End Call also uses
        assert ev["disposition"] == "Correct Number"


def test_canary_only_texts_allowlisted_team_phones():
    from fastapi.testclient import TestClient

    from src.sms_agent import config, receiver, transport
    from src.sms_agent.smrtphone import SendResult
    sent = []
    old = (config.WEBHOOK_SECRET, config.CANARY_NUMBERS, transport.send, config.SENDER_NAME)
    config.WEBHOOK_SECRET, config.CANARY_NUMBERS, config.SENDER_NAME = "s3cret", ["+12085551234"], "Nick"
    transport.send = lambda to, body, frm="": sent.append((to, frm)) or SendResult(True, sms_id="sm1")
    try:
        c = TestClient(receiver.app)
        assert c.post("/hooks/wrong/canary?to=2085551234").status_code == 404
        assert c.post("/hooks/s3cret/canary?to=2085559999").status_code == 403  # a seller, say
        r = c.post("/hooks/s3cret/canary?to=(208) 555-1234")
        assert r.status_code == 200 and r.json()["ok"], r.text
    finally:
        config.WEBHOOK_SECRET, config.CANARY_NUMBERS, transport.send, config.SENDER_NAME = old
    assert sent == [("2085551234", "+12085550100")], sent  # from Nick's pool number
    assert store.get_conversation("2085551234")["from_number"] == "+12085550100"


def test_call_log_window_excludes_other_days():
    assert call_log.calls_between("2000-01-01T00:00:00", "2000-01-02T00:00:00") == []


def _audit_module():
    import kixie_daily_audit as A
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
        "Expenses": [["DATE", "Expense Type", "DESCRIPTION", "AMOUNT"], [d, "Software", "Kixie", "$95"]],
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
