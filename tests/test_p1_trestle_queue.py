"""P1 Manager Trestle watcher (src/p1_manager/trestle_queue.py), zero network.

Pins which Autotext leads go on the dated Trestle list (unscored ones only),
that the P1 channel gets ONE approvable post the moment they're found, that a
queued lead is not re-posted every sweep, and that the scheduled enrichment
scan never previews the same list again.

Run: python tests/test_p1_trestle_queue.py   (or pytest)
"""
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["SIFTSTACK_STATE_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(ROOT / "src" / "p1_manager"))

import enrich  # noqa: E402
import state  # noqa: E402
import trestle_queue as tq  # noqa: E402

tq.ADD_SLEEP = 0
DAY = date(2026, 10, 21)


def _phone(num, *tags):
    return {"number": num, "tags": [{"title": t} for t in tags]}


def _rec(phones=(), traced=True, tags=()):
    return {"owner": {"uuid": "o", "phones": list(phones)}, "skiptraced": traced,
            "tags": [{"name": t} for t in tags]}


class FakeClient:
    def __init__(self, tag_title="AutoText"):
        self.tags = [{"uuid": "T1", "title": tag_title}, {"uuid": "T2", "title": "P1"}]
        self.lists = [{"uuid": "L0", "title": "Old list"}]
        self.records = {
            "ok": _rec([_phone("2085550001", "Dial First"), _phone("2085550002")]),
            "low": _rec([_phone("2085550003", "Dial Third")]),
            "fresh": _rec([_phone("2085550004")]),
            "rec_only": _rec([_phone("2085550005")], tags=["Dial First"]),
            "untraced": _rec([], traced=False),
            "dead_end": _rec([], traced=True),
        }
        self.tagged = list(self.records)
        self.added = []
        self.created = []
        self.reads = 0

    def api(self, path, method="GET", body=None, **_):
        if path == "/api/internal/tag/?limit=999":
            return {"results": self.tags}
        if path.startswith("/api/internal/list/") and method == "GET":
            return {"results": self.lists}
        if path == "/api/internal/list/" and method == "POST":
            new = {"uuid": f"L{len(self.lists)}", "title": body["title"]}
            self.lists.append(new)
            self.created.append(body["title"])
            return new
        if path.endswith("/add-lists/"):
            assert isinstance(body["lists"], str)  # an array 201s and does nothing
            self.added.append((path.split("/")[-3], body["lists"]))
            return {}
        self.reads += 1
        return self.records[path.rstrip("/").rsplit("/", 1)[-1]]

    def search_properties(self, must, **_):
        if "any_lists" in must:
            return [{"uuid": u} for u, t in self.added if t == must["any_lists"][0]]
        assert must == {"any_tags": ["T1"]}, must
        return [{"uuid": u} for u in self.tagged]


def _reset():
    for f in (state.TRESTLE_STATE_FILE, state.ENRICH_STATE_FILE, state.PENDING_ASKS_FILE):
        if f.exists():
            f.unlink()


def test_classify():
    c = FakeClient()
    got = {u: tq.classify(r) for u, r in c.records.items()}
    assert got == {"ok": "textable", "low": "low_tier", "fresh": "needs_scoring",
                   "rec_only": "needs_scoring", "untraced": "needs_scoring",
                   "dead_end": "no_phone"}, got


def test_one_approvable_post_the_moment_leads_are_found():
    _reset()
    c, posts = FakeClient(), []
    out = tq.sweep(lambda t: posts.append(t) or "ts1", client=c, today=DAY)
    assert c.created == ["Trestle - 10-21-2026"]
    assert sorted(u for u, _ in c.added) == ["fresh", "rec_only", "untraced"], c.added
    assert out["added"] == 3 and out["ts"] == "ts1", out
    assert len(posts) == 1, posts
    text = posts[0]
    assert "3 lead(s) tagged Autotext" in text and "Trestle - 10-21-2026" in text
    assert "need to be Trestled" in text
    assert "2 records* have phones to score" in text and "never been skip traced" in text
    assert "approve" in text
    assert "1 newly tagged were already scored Dial Third" in text
    assert "1 have no phone even after a skip trace" in text
    # The post IS the approval thread, scoped to exactly these leads.
    ask = state.load_pending_asks()["ts1"]
    assert ask["kind"] == "enrich" and ask["status"] == "pending"
    assert set(ask["records"]) == {"fresh", "rec_only"} and ask["needs_trace"] == ["untraced"]
    # The scheduled scan must not preview this list a second time.
    lst = [x for x in c.lists if x["title"] == "Trestle - 10-21-2026"][0]
    assert state.load_enrich_state()["handled"][lst["uuid"]]["status"] == "trestle_queue"


def test_next_sweep_reads_nothing_and_posts_nothing():
    _reset()
    c, posts = FakeClient(), []
    tq.sweep(lambda t: posts.append(t) or "ts1", client=c, today=DAY)
    reads = c.reads
    out = tq.sweep(lambda t: posts.append(t) or "ts2", client=c, today=DAY)
    assert out == {"autotext": 6, "checked": 0} and c.reads == reads and len(posts) == 1


def test_a_newly_tagged_lead_is_posted_on_its_own():
    _reset()
    c, posts = FakeClient(), []
    tq.sweep(lambda t: posts.append(t) or "ts1", client=c, today=DAY)
    c.records["new"] = _rec([_phone("2085550099")])
    c.tagged.append("new")
    out = tq.sweep(lambda t: posts.append(t) or "ts2", client=c, today=DAY)
    assert out["checked"] == 1 and out["added"] == 1, out
    assert c.created == ["Trestle - 10-21-2026"]  # same day, same list
    assert "1 lead(s) tagged Autotext" in posts[-1]
    assert "newly tagged were already scored" not in posts[-1]
    assert set(state.load_pending_asks()["ts2"]["records"]) == {"new"}


def test_rechecks_daily_and_requeues_after_a_week():
    _reset()
    c, posts = FakeClient(), []
    tq.sweep(lambda t: posts.append(t) or "ts1", client=c, today=DAY)
    # Next day: the scored ones are re-read, the queued ones are left alone.
    out = tq.sweep(lambda t: posts.append(t) or "ts2", client=c, today=date(2026, 10, 22))
    assert out["checked"] == 3 and out["to_add"] == 0 and len(posts) == 1, out
    # A week on, still unscored: queued again on that day's list.
    out = tq.sweep(lambda t: posts.append(t) or "ts3", client=c, today=date(2026, 10, 28))
    assert out["added"] == 3 and c.created[-1] == "Trestle - 10-28-2026", out


def test_missing_tag_warns_once_a_day():
    _reset()
    c, posts = FakeClient(tag_title="Something Else"), []
    tq.sweep(posts.append, client=c, today=DAY)
    tq.sweep(posts.append, client=c, today=DAY)
    assert len(posts) == 1 and "Autotext" in posts[0] and not c.added


def test_dry_run_writes_nothing():
    _reset()
    c, posts = FakeClient(), []
    out = tq.sweep(posts.append, client=c, today=DAY, commit=False)
    assert out["to_add"] == 3 and not c.added and not c.created and not posts
    assert not state.load_trestle_state()


def test_scheduled_scan_skips_the_watchers_list():
    _reset()
    c = FakeClient()
    enrich.run_preview(lambda t: "ts0", client=c)  # baseline
    tq.sweep(lambda t: "ts1", client=c, today=DAY)
    lists, _ = enrich.new_lists(c)
    assert lists == [], lists


def test_active_hours():
    tz = tq._tz()
    assert tq._active(datetime(2026, 10, 21, 9, 0, tzinfo=tz))
    assert not tq._active(datetime(2026, 10, 21, 21, 0, tzinfo=tz))
    assert not tq._active(datetime(2026, 10, 21, 6, 59, tzinfo=tz))


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} passed")
