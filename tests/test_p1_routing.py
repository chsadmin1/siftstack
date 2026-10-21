"""P1 Manager auto-routing to Raw / <county> (src/p1_manager/routing.py), zero network.

Pins that the weekly job puts new records on the county's Raw phase by
itself, skips records already on the Raw board, says where they went in
Slack, and falls back to asking when the county or phase can't be found.

Run: python tests/test_p1_routing.py   (or pytest)
"""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["SIFTSTACK_STATE_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(ROOT / "src" / "p1_manager"))

import routing  # noqa: E402
import slack_app  # noqa: E402
import state  # noqa: E402
import weekly  # noqa: E402

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _preset(pid, name, county):
    return {"id": pid, "name": name,
            "filter_data": {"filters": {}, "addresses": [{"county": county, "state": "ID"}]}}


class FakeClient:
    def __init__(self):
        self.presets = {
            1: _preset(1, "P1 - Free & Clear + Senior + Tired Landlord — Canyon County, ID",
                       "Canyon County"),
            2: _preset(2, "P1 - Other Lien — Ada County, ID", "Ada"),
        }
        self.rows = {
            1: [{"saved": True, "saved_date": NOW, "saved_uuid": "p-nampa",
                 "saved_tags": ["P1"], "address": "1412 11th Ave S, Nampa, ID 83651"}],
            2: [{"saved": True, "saved_date": NOW, "saved_uuid": u, "saved_tags": ["P1"],
                 "address": f"{u} Boise"} for u in ("p-a1", "p-a2", "p-moved")],
        }
        self._searching = None
        self.cards = {"p-moved": [{"board": {"uuid": "b-raw"}, "column": {"uuid": "c-follow"}}]}
        self.created = []
        self.cols = {"b-raw": [{"uuid": "c-canyon", "title": "Canyon"},
                               {"uuid": "c-ada", "name": "Ada"},
                               {"uuid": "c-follow", "title": "Follow Up"}]}

    def list_presets(self):
        return [{"id": p["id"], "name": p["name"]} for p in self.presets.values()]

    def get_preset(self, pid):
        self._searching = pid
        return self.presets[pid]

    def map_call(self, path, method="GET", body=None, **_):
        return {"results": self.rows[self._searching]}

    def boards(self):
        return [{"uuid": "b-acq", "title": "Acquisitions"}, {"uuid": "b-raw", "title": "RAW"}]

    def board_columns(self, uuid):
        return self.cols.get(uuid, [])

    def property_cards(self, uuid):
        return self.cards.get(uuid, [])

    def create_siftline_card(self, column_uuid, property_uuid):
        self.created.append((column_uuid, property_uuid))
        return {}


def test_county_of():
    assert routing.county_of(_preset(1, "x", "Canyon County")) == "Canyon"
    assert routing.county_of(_preset(1, "x", "Ada")) == "Ada"
    assert routing.county_of({}) == ""


def test_route_places_and_skips_cards_already_on_raw():
    c = FakeClient()
    out = routing.route(c, c.presets[2], ["p-a1", "p-a2", "p-moved"])
    assert out["board"] == "RAW" and out["phase"] == "Ada", out
    assert (out["created"], out["skipped"], out["failed"]) == (2, 1, 0)
    assert c.created == [("c-ada", "p-a1"), ("c-ada", "p-a2")]


def test_missing_phase_is_an_error_not_a_guess():
    c = FakeClient()
    out = routing.route(c, _preset(3, "P1 - x — Gem County, ID", "Gem County"), ["p-g"])
    assert "no phase named Gem" in out["error"] and not c.created


def test_weekly_run_routes_by_county_and_says_so():
    c = FakeClient()
    posts = []
    orig = weekly.DataSiftClient.from_env
    weekly.DataSiftClient.from_env = classmethod(lambda cls: c)
    try:
        out = weekly.run(post_slack=lambda name, rows, routed: posts.append((name, routed)) or
                         f"ts-{len(posts)}", update_cursor=False)
    finally:
        weekly.DataSiftClient.from_env = orig
    assert ("c-canyon", "p-nampa") in c.created and ("c-ada", "p-a1") in c.created, c.created
    routed = {name.split(" — ")[1]: r for name, r in posts}
    assert routed["Canyon County, ID"]["phase"] == "Canyon"
    assert routed["Ada County, ID"]["created"] == 2 and routed["Ada County, ID"]["skipped"] == 1
    # A reply naming the same board+phase later is a no-op, not a duplicate.
    asks = state.load_pending_asks()
    assert any(a.get("resolved_targets") == ["b-raw/c-canyon"] for a in asks.values()), asks
    assert out["presets_with_new_records"] == 2


def test_slack_post_says_where_they_went():
    sent = []
    orig_call, orig_env = slack_app._call, slack_app._env
    slack_app._call = lambda method, payload: sent.append(payload["text"]) or {"ok": True, "ts": "1"}
    slack_app._env = lambda name: "x"
    try:
        slack_app.post_weekly_ask("P1 - Other Lien — Ada County, ID", [{"address": "a"}],
                                  {"board": "Raw", "phase": "Ada", "board_uuid": "b",
                                   "column_uuid": "c", "created": 15, "skipped": 1, "failed": 0})
        slack_app.post_weekly_ask("P1 - x", [{"address": "a"}],
                                  {"error": "no phase named Gem on the Raw board"})
    finally:
        slack_app._call, slack_app._env = orig_call, orig_env
    assert "Added 15 to *Raw / Ada*" in sent[0] and "1 already on the Raw board" in sent[0]
    assert "Should these go" not in sent[0]
    assert "no phase named Gem" in sent[1] and "Should these go" in sent[1]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} passed")
