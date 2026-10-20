"""P1 Manager Trestle queue (src/p1_manager/trestle_queue.py), zero network.

Pins which Autotext leads go on the dated Trestle list (unscored ones only),
that the P1 channel is told why, that a lead is not re-queued every morning,
and that the job runs right before the enrichment preview so the new list is
in that same preview.

Run: python tests/test_p1_trestle_queue.py   (or pytest)
"""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["SIFTSTACK_STATE_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(ROOT / "src" / "p1_manager"))

import state  # noqa: E402
import trestle_queue as tq  # noqa: E402

tq.ADD_SLEEP = 0
DAY = date(2026, 10, 20)


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
        self.added = []
        self.created = []

    def api(self, path, method="GET", body=None, **_):
        if path == "/api/internal/tag/?limit=999":
            return {"results": self.tags}
        if path == "/api/internal/list/?limit=999":
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
        return self.records[path.rstrip("/").rsplit("/", 1)[-1]]

    def search_properties(self, must, **_):
        assert must == {"any_tags": ["T1"]}, must
        return [{"uuid": u} for u in self.records]


def _reset():
    if state.TRESTLE_STATE_FILE.exists():
        state.TRESTLE_STATE_FILE.unlink()


def test_classify():
    c = FakeClient()
    got = {u: tq.classify(r) for u, r in c.records.items()}
    assert got == {"ok": "textable", "low": "low_tier", "fresh": "needs_scoring",
                   "rec_only": "needs_scoring", "untraced": "needs_scoring",
                   "dead_end": "no_phone"}, got


def test_queues_unscored_leads_and_tells_the_channel():
    _reset()
    c, posts = FakeClient(), []
    out = tq.run(posts.append, client=c, today=DAY)
    assert c.created == ["Trestle - 10-20-2026"]
    assert sorted(u for u, _ in c.added) == ["fresh", "rec_only", "untraced"], c.added
    assert {t for _, t in c.added} == {"Trestle - 10-20-2026"}
    assert out["added"] == 3 and out["autotext"] == 6, out
    text = posts[0]
    assert "3 lead(s) tagged Autotext" in text and "Trestle - 10-20-2026" in text
    assert "need to be Trestled" in text and "approve" in text
    assert "1 were already scored Dial Third or lower" in text
    assert "1 have no phone even after a skip trace" in text


def test_once_a_day_and_no_requeue_while_waiting():
    _reset()
    c, posts = FakeClient(), []
    tq.run(posts.append, client=c, today=DAY)
    assert tq.run(posts.append, client=c, today=DAY) == {"skipped": "already ran today"}
    # Next morning: the same three are still unscored but already waiting.
    out = tq.run(posts.append, client=c, today=date(2026, 10, 21))
    assert out["to_add"] == 0 and out["recently_queued"] == 3, out
    assert len(c.added) == 3 and len(posts) == 1  # nothing re-added, nothing re-posted
    # A week on, still unscored: queued again on that day's list.
    out = tq.run(posts.append, client=c, today=date(2026, 10, 28))
    assert out["added"] == 3 and c.created[-1] == "Trestle - 10-28-2026", out


def test_reuses_todays_list_when_it_exists():
    _reset()
    c = FakeClient()
    c.lists.append({"uuid": "L9", "title": "Trestle - 10-20-2026"})
    tq.run(lambda t: None, client=c, today=DAY)
    assert c.created == [] and len(c.added) == 3


def test_missing_tag_says_so():
    _reset()
    c, posts = FakeClient(tag_title="Something Else"), []
    out = tq.run(posts.append, client=c, today=DAY)
    assert "error" in out and "Autotext" in posts[0] and not c.added


def test_dry_run_writes_nothing():
    _reset()
    c, posts = FakeClient(), []
    out = tq.run(posts.append, client=c, today=DAY, commit=False)
    assert out["to_add"] == 3 and not c.added and not c.created and not posts
    assert not state.load_trestle_state()


def test_runs_before_the_enrichment_preview():
    import enrich
    import schedule
    import slack_app

    calls = []
    saved = (tq.run, enrich.run_followups, enrich.run_preview, slack_app.post_to_channel)
    tq.run = lambda post, **k: calls.append("trestle") or {}
    enrich.run_followups = lambda reply: calls.append("followups") or 0
    enrich.run_preview = lambda post: calls.append("preview") or {}
    for k in ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CHANNEL_ID"):
        os.environ.setdefault(k, "x")
    try:
        schedule.run_enrich()
        os.environ["P1_TRESTLE_QUEUE"] = "0"
        schedule.run_enrich()
    finally:
        tq.run, enrich.run_followups, enrich.run_preview, slack_app.post_to_channel = saved
        os.environ.pop("P1_TRESTLE_QUEUE", None)
    assert calls == ["trestle", "followups", "preview", "followups", "preview"], calls


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} passed")
