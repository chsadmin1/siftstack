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
        self.deleted = []
        self.preset_updates = []
        self.presets[2]["replace_owners_enabled"] = True  # already on
        self.cols = {"b-lm": [{"uuid": "c-nc", "title": "No Contact"},
                              {"uuid": "c-new", "title": "New Lead"}],
                     "b-raw": [{"uuid": "c-canyon", "title": "Canyon"},
                               {"uuid": "c-ada", "name": "Ada"},
                               {"uuid": "c-follow", "title": "Follow Up"}]}

    def update_preset(self, pid, **fields):
        self.preset_updates.append((pid, fields))
        self.presets[pid].update(fields)
        return self.presets[pid]

    def list_presets(self):
        return [{"id": p["id"], "name": p["name"]} for p in self.presets.values()]

    def get_preset(self, pid):
        self._searching = pid
        return self.presets[pid]

    def map_call(self, path, method="GET", body=None, **_):
        return {"results": self.rows[self._searching]}

    def boards(self):
        return [{"uuid": "b-acq", "title": "Acquisitions"}, {"uuid": "b-raw", "title": "RAW"},
                {"uuid": "b-lm", "title": "Lead Management"}]

    def board_columns(self, uuid):
        return self.cols.get(uuid, [])

    def property_cards(self, uuid):
        return self.cards.get(uuid, [])

    def create_siftline_card(self, column_uuid, property_uuid):
        self.created.append((column_uuid, property_uuid))
        board = next(b for b, cols in self.cols.items()
                     if any(c["uuid"] == column_uuid for c in cols))
        self.cards.setdefault(property_uuid, []).append(
            {"uuid": f"card-{len(self.created)}", "board": {"uuid": board},
             "column": {"uuid": column_uuid}})
        return {}

    def delete_siftline_card(self, column_uuid, card_uuid):
        self.deleted.append(card_uuid)
        for cards in self.cards.values():
            cards[:] = [c for c in cards if c.get("uuid") != card_uuid]


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
    assert "Automatically put 15 on *Raw / Ada*" in sent[0]
    assert "1 were already on the Raw board" in sent[0]
    assert "move them to a different board" in sent[0]
    assert "Lead Management / No Contact" in sent[0] and "Lead Management / New Lead" in sent[0]
    assert "Should these go" not in sent[0]
    assert "no phase named Gem" in sent[1] and "Should these go" in sent[1]


def test_reply_moves_the_batch_off_raw():
    c = FakeClient()
    orig = weekly.DataSiftClient.from_env
    weekly.DataSiftClient.from_env = classmethod(lambda cls: c)
    ts = {}
    try:
        weekly.run(post_slack=lambda name, rows, routed: ts.setdefault(
                       name.split(" \u2014 ")[1], f"ts-{len(ts)}"),
                   update_cursor=False, only_presets=[c.presets[2]["name"]])
    finally:
        weekly.DataSiftClient.from_env = orig
    thread = ts["Ada County, ID"]
    # Someone already moved p-a2 along inside Raw before the reply.
    for card in c.cards["p-a2"]:
        card["column"] = {"uuid": "c-follow"}
    sent = []
    saved = (slack_app.DataSiftClient.from_env, slack_app._call)
    slack_app.DataSiftClient.from_env = classmethod(lambda cls: c)
    slack_app._call = lambda method, payload: sent.append(payload["text"]) or {"ok": True}
    try:
        slack_app.handle_reply(thread, "Lead Management / No Contact", "C1")
        slack_app.handle_reply(thread, "Lead Management / New Lead", "C1")
    finally:
        slack_app.DataSiftClient.from_env, slack_app._call = saved

    def boards_of(u):
        return sorted(card["board"]["uuid"] + "/" + card["column"]["uuid"] for card in c.cards[u])

    assert boards_of("p-a1") == ["b-lm/c-nc", "b-lm/c-new"], boards_of("p-a1")  # off Raw
    assert "b-raw/c-follow" in boards_of("p-a2")  # moved within Raw: left alone
    assert "b-raw/c-follow" in boards_of("p-moved")  # was never ours
    assert "Moved 1 card(s) from *RAW / Ada* to *Lead Management / No Contact*" in sent[0], sent
    assert "2 were in another Raw phase and were left there" in sent[0]
    assert sent[1].startswith(":white_check_mark: Added 3 card(s) to *Lead Management / New Lead*")


def test_daily_run_turns_on_swap_owners_and_offers_trestle():
    c = FakeClient()
    offers = []
    orig = weekly.DataSiftClient.from_env
    weekly.DataSiftClient.from_env = classmethod(lambda cls: c)
    try:
        out = weekly.run(post_slack=lambda name, rows, routed: "ts", update_cursor=False,
                         phone_preview=lambda client, uuids: offers.append(uuids) or "ts-phone")
    finally:
        weekly.DataSiftClient.from_env = orig
    # Only the preset that had it off is changed, and only that one field.
    assert c.preset_updates == [(1, {"replace_owners_enabled": True})], c.preset_updates
    assert out["swap_owners_turned_on"] == [c.presets[1]["name"]]
    # One offer for the whole run, every new record once.
    assert offers == [["p-nampa", "p-a1", "p-a2", "p-moved"]], offers
    assert out["phone_offer_ts"] == "ts-phone"


def test_swap_owners_can_be_turned_off():
    c = FakeClient()
    os.environ["P1_REPLACE_OWNERS"] = "0"
    orig = weekly.DataSiftClient.from_env
    weekly.DataSiftClient.from_env = classmethod(lambda cls: c)
    try:
        weekly.run(post_slack=None, update_cursor=False)
    finally:
        weekly.DataSiftClient.from_env = orig
        del os.environ["P1_REPLACE_OWNERS"]
    assert c.preset_updates == []


def test_trestle_offer_for_new_p1_records():
    import enrich

    class C:
        def api(self, path, *a, **k):
            u = path.rstrip("/").rsplit("/", 1)[-1]
            return {"p-1": {"owner": {"phones": [{"number": "2085550101"}]}, "skiptraced": True},
                    "p-2": {"owner": {"phones": []}, "skiptraced": False},
                    "p-3": {"owner": {"phones": [{"number": "2085550103",
                                                  "tags": [{"title": "Dial First"}]}]}}}[u]

    posts = []
    ts = enrich.preview_new_p1(lambda t: posts.append(t) or "ts-x", C(), ["p-1", "p-2", "p-3"])
    assert ts == "ts-x" and "today's 3 new P1 record(s)" in posts[0]
    assert "refreshed from DataSift" in posts[0] and "approve" in posts[0]
    ask = state.load_pending_asks()["ts-x"]
    assert ask["kind"] == "enrich" and ask["list_uuids"] == []
    assert set(ask["records"]) == {"p-1"} and ask["needs_trace"] == ["p-2"]  # p-3 already scored


def test_daily_schedule():
    import schedule

    os.environ["P1_WEEKLY_DAY"] = "daily"
    os.environ["P1_WEEKLY_AT"] = "06:00"
    try:
        slot = [s for s in schedule.slots() if s.name == "weekly"][0]
        assert (slot.hh, slot.mm, slot.weekdays) == (6, 0, (0, 1, 2, 3, 4, 5, 6))
        tz = schedule._tz()
        nxt = slot.next_after(datetime(2026, 10, 24, 7, 0, tzinfo=tz))  # a Saturday, after 6
        assert nxt.day == 25 and nxt.hour == 6
    finally:
        del os.environ["P1_WEEKLY_DAY"], os.environ["P1_WEEKLY_AT"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} passed")
