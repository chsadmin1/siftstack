"""P1 Manager phone enrichment (src/p1_manager/enrich.py), zero network.

A fake DataSift client and a fake Trestle stand in for the real services, so
these pin what the job decides: which lists count as new, what the preview
charges, that nothing is scored before "approve", that tags go out through
the safe bulk call grouped by tier, and that a failed Trestle call never
becomes a "Drop" tag.

Run: python tests/test_p1_enrich.py   (or pytest)
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["SIFTSTACK_STATE_DIR"] = tempfile.mkdtemp()
os.environ["TRESTLE_API_KEY"] = "test-key"
sys.path.insert(0, str(ROOT / "src" / "p1_manager"))

import enrich  # noqa: E402
import schedule  # noqa: E402
import state  # noqa: E402


class FakeClient:
    def __init__(self):
        self.lists = [{"uuid": "L1", "title": "Old list"}]
        self.list_members = {"L1": ["r0"]}
        self.records = {}
        self.tag_calls = []
        self.stats = {"value_spent": 0.0, "saved_by_unlimited_skiptracing": 511.2}
        self.trace_calls = []
        self.phone_writes = []

    def api(self, path, method="GET", body=None, **_):
        if path.endswith("/upsert-phones/"):
            # Replaces the phone object by number, like the real endpoint.
            for new in body["phones"]:
                self.phone_writes.append(new)
                for rec in self.records.values():
                    for o in rec["owners"]:
                        for i, old in enumerate(o["phones"]):
                            if enrich._phone_number(old) == new["number"]:
                                o["phones"][i] = {**new, "tags": [{"title": t} for t in new["tags"]]}
            return {}
        if path.startswith("/api/internal/list/"):
            return {"results": self.lists}
        if path == enrich.SKIPTRACE_STATS:
            return self.stats
        if path == enrich.SKIPTRACE_RUN:
            self.trace_calls.append(body)
            return {}
        uuid = path.rstrip("/").rsplit("/", 1)[-1]
        return self.records[uuid]

    def search_properties(self, must, **_):
        return [{"uuid": u} for u in self.list_members.get(must["any_lists"][0], [])]

    def bulk_add_tags(self, uuids, tags, **_):
        self.tag_calls.append((sorted(uuids), tags))
        return len(uuids)


def _rec(phones, tags=(), traced=True, phone_tags=None):
    phone_tags = phone_tags or {}
    return {"owners": [{"uuid": "o1", "phones": [
                {"phone": p, "tags": [{"title": t} for t in phone_tags.get(p, [])]}
                for p in phones]}],
            "tags": [{"name": t} for t in tags], "skiptraced": traced}


def _reset():
    for f in (state.ENRICH_STATE_FILE, state.PENDING_ASKS_FILE):
        if f.exists():
            f.unlink()


def test_first_run_seeds_baseline_and_charges_nothing():
    _reset()
    c, posts = FakeClient(), []
    out = enrich.run_preview(lambda t: posts.append(t) or "ts0", client=c)
    assert out == {"seeded": 1}
    assert "watcher is on" in posts[0]
    assert state.load_enrich_state()["handled"]["L1"]["status"] == "baseline"
    assert not state.load_pending_asks()


def _seeded_client():
    _reset()
    c = FakeClient()
    enrich.run_preview(lambda t: "ts0", client=c)  # baseline
    c.lists.append({"uuid": "L2", "title": "Ada Probate Oct"})
    c.list_members["L2"] = ["r1", "r2", "r3", "r4"]
    c.records = {
        "r1": _rec(["(208) 555-0101", "2085550102"]),
        "r2": _rec(["+1 208 555 0101"]),                  # same number as r1: scored once
        "r3": _rec([], traced=False),                      # never traced
        "r4": _rec(["2085550104"], phone_tags={"2085550104": ["Dial Second"]}),  # already tiered
    }
    return c


def test_preview_counts_unique_phones_and_waits_for_approval():
    c, posts = _seeded_client(), []
    out = enrich.run_preview(lambda t: posts.append(t) or "ts1", client=c)
    assert out["phones"] == 2 and out["est_cost"] == 0.03, out
    text = posts[-1]
    assert "Ada Probate Oct" in text and "2 unique numbers" in text and "$0.03" in text
    assert "1 records have never been skip traced" in text and "account" in text
    assert "1 records already have a dial tier" in text
    ask = state.load_pending_asks()["ts1"]
    assert ask["kind"] == "enrich" and ask["status"] == "pending"
    assert set(ask["records"]) == {"r1", "r2"} and ask["needs_trace"] == ["r3"]
    assert c.tag_calls == [] and c.trace_calls == []  # nothing written, scored or traced
    # The 12 PM run must not offer the same list again while it waits.
    again = enrich.run_preview(lambda t: posts.append(t) or "ts2", client=c)
    assert again == {"new_lists": 0}


def test_skip_marks_lists_handled_without_scoring():
    c = _seeded_client()
    enrich.run_preview(lambda t: "ts1", client=c)
    replies = []
    enrich.handle_reply("ts1", "skip", state.load_pending_asks()["ts1"], replies.append)
    assert "Skipped" in replies[0]
    assert state.load_enrich_state()["handled"]["L2"]["status"] == "skipped"
    assert state.load_pending_asks()["ts1"]["status"] == "skipped"


def test_approve_scores_tags_by_tier_and_reports_gate():
    c = _seeded_client()
    enrich.run_preview(lambda t: "ts1", client=c)
    scores = {"2085550101": 90, "2085550102": None}  # second call fails
    orig_score, orig_client = enrich.trestle_score, enrich.DataSiftClient.from_env
    enrich.trestle_score = lambda p, k, s: scores[p]
    enrich.DataSiftClient.from_env = classmethod(lambda cls: c)
    replies = []
    try:
        enrich.handle_reply("ts1", "Approve", state.load_pending_asks()["ts1"], replies.append)
        for _ in range(50):
            if len(replies) >= 2:
                break
            time.sleep(0.05)
    finally:
        enrich.trestle_score, enrich.DataSiftClient.from_env = orig_score, orig_client

    assert "Approved" in replies[0] and "skip trace for 1 records" in replies[0], replies
    assert c.trace_calls == [{}]  # the account-wide call, once
    assert c.tag_calls == [(["r1", "r2"], ["Dial First"])], c.tag_calls
    # The tier also lands on the phone itself, which is what auto-texting reads.
    r1_phones = {enrich._phone_number(p): enrich._names(p["tags"])
                 for p in c.records["r1"]["owners"][0]["phones"]}
    assert r1_phones == {"2085550101": {"Dial First"}, "2085550102": set()}, r1_phones
    summary = replies[1]
    assert "Tagged 2 of 2 numbers with their own tier" in summary, summary
    assert "Scored 1 numbers" in summary and "tagged 2 records" in summary
    assert "Skip trace started for 1 records" in summary
    assert "1 numbers could not be scored" in summary
    assert "Quality gate not met" in summary  # 2 of 3 have phones is 67%, and only 2 top tier
    ask = state.load_pending_asks()["ts1"]
    assert ask["status"] == "done" and ask["awaiting"] == ["r3"]
    assert state.load_enrich_state()["handled"]["L2"]["status"] == "enriched"
    # A second "approve" does not run it again.
    enrich.handle_reply("ts1", "approve", state.load_pending_asks()["ts1"], replies.append)
    assert "already done" in replies[-1]


def test_skip_trace_follow_up_scores_new_numbers():
    c = _seeded_client()
    enrich.run_preview(lambda t: "ts1", client=c)
    pending = state.load_pending_asks()["ts1"]
    orig_score, orig_client = enrich.trestle_score, enrich.DataSiftClient.from_env
    enrich.trestle_score = lambda p, k, s: {"2085550101": 90, "2085550102": 70, "2085550300": 65}.get(p)
    enrich.DataSiftClient.from_env = classmethod(lambda cls: c)
    posted = []
    try:
        enrich.run_enrichment(pending, client=c)
        pending["status"] = "done"
        state.save_pending_ask("ts1", pending)
        # Results not in yet: nothing posted, still waiting.
        assert enrich.run_followups(lambda ts, t: posted.append((ts, t))) == 0
        c.records["r3"] = _rec(["2085550300"])  # the trace landed
        assert enrich.run_followups(lambda ts, t: posted.append((ts, t))) == 1
    finally:
        enrich.trestle_score, enrich.DataSiftClient.from_env = orig_score, orig_client
    assert posted[0][0] == "ts1" and "1 records came back with numbers" in posted[0][1]
    assert c.tag_calls[-1] == (["r3"], ["Dial Second"]), c.tag_calls
    ask = state.load_pending_asks()["ts1"]
    assert ask["awaiting"] == [] and ask["tiers"]["r3"] == "Dial Second"
    assert "100% of records have phones" in posted[0][1]


def test_metered_plan_refuses_skip_trace():
    c = FakeClient()
    c.stats = {"value_spent": 12.5, "saved_by_unlimited_skiptracing": 0}
    ok, why = enrich.start_skip_trace(c)
    assert not ok and "whole account" in why and c.trace_calls == []


def test_failed_phone_never_becomes_drop():
    orig = enrich.trestle_score
    enrich.trestle_score = lambda p, k, s: None
    try:
        tiers, stats = enrich.score_records({"r": ["2085550199"]}, "k")
    finally:
        enrich.trestle_score = orig
    assert tiers == {} and stats == {"scored": 0, "failed": 1, "phone_tiers": {}}


def test_record_level_tier_alone_is_rescored():
    """Enrichment before 2026-10-20 tagged only the record. The texting
    assistant reads phone tags, so such a record must be scored again."""
    c = FakeClient()
    c.records = {"rA": _rec(["2085550401"], tags=["Dial First"]),
                 "rB": _rec(["2085550402"], phone_tags={"2085550402": ["Dial Third"]})}
    c.list_members["LX"] = ["rA", "rB"]
    out = enrich.collect(c, [{"uuid": "LX", "title": "x"}])
    assert set(out["records"]) == {"rA"} and out["already_tiered"] == 1, out


def test_phone_payload_keeps_everything_but_the_tier():
    p = {"number": "(208) 555-0501", "type": "mobile", "status": "CORRECT",
         "is_connected": False, "verified": True,
         "tags": [{"title": "Litigator"}, {"title": "Dial Fourth"}]}
    got = enrich._phone_payload(p, "Dial First")
    assert got == {"number": "2085550501", "type": "MOBILE", "status": "CORRECT",
                   "is_connected": False, "verified": True,
                   "tags": ["Litigator", "Dial First"]}, got


def test_tiers_and_gate():
    assert [enrich.tier_for(s) for s in (100, 81, 80, 61, 41, 21, 20, 0)] == [
        "Dial First", "Dial First", "Dial Second", "Dial Second", "Dial Third",
        "Dial Fourth", "Drop", "Drop"]
    g = enrich.gate({f"r{i}": "Dial First" for i in range(18)}, total_with_phones=20, no_phone=5)
    assert g["passed"] and g["top_tier"] == 18
    assert not enrich.gate({f"r{i}": "Dial First" for i in range(18)}, 20, 10)["passed"]  # 67%


def test_schedule_has_weekday_enrich_slots_in_boise():
    os.environ["P1_TIMEZONE"] = "America/Boise"
    names = [(s.name, s.hh, s.mm, s.weekdays) for s in schedule.slots()]
    assert ("enrich", 7, 0, (0, 1, 2, 3, 4)) in names and ("enrich", 12, 0, (0, 1, 2, 3, 4)) in names
    from datetime import datetime
    tz = schedule._tz()
    sat = datetime(2026, 10, 10, 9, 0, tzinfo=tz)  # a Saturday
    nxt = [s for s in schedule.slots() if s.name == "enrich"][0].next_after(sat)
    assert nxt.weekday() == 0 and nxt.hour == 7, nxt
    os.environ["P1_ENRICH_AT"] = "off"
    assert all(s.name != "enrich" for s in schedule.slots())
    del os.environ["P1_ENRICH_AT"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} passed")
