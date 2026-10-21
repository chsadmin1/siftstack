"""P1 Manager weekly-ask reply matching (slack_app.resolve_board_phase), zero network.

Pins that a reply only moves cards on a confident board + phase match, that
dash styles and a pasted SiftLine link don't get in the way, and that a reply
missing the phase says which phases exist instead of a bare error.

Run: python tests/test_p1_board_reply.py   (or pytest)
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["SIFTSTACK_STATE_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(ROOT / "src" / "p1_manager"))

import slack_app  # noqa: E402

RAW = "eab952d2-97b0-4632-81e2-cf50350d35eb"


class FakeClient:
    def __init__(self):
        self._boards = [
            {"uuid": RAW, "title": "Raw - Canyon"},
            {"uuid": "b-raw", "title": "Raw"},
            {"uuid": "b-acq", "title": "Acquisitions"},
            {"uuid": "b-one", "title": "Solo Board"},
        ]
        self._cols = {
            RAW: [{"uuid": "c1", "title": "New Lead"}, {"uuid": "c2", "title": "Follow Up"}],
            "b-raw": [{"uuid": "c3", "title": "New Lead"}],
            "b-acq": [{"uuid": "c4", "title": "Make Offer"}, {"uuid": "c5", "title": "Follow Up"}],
            "b-one": [{"uuid": "c6", "title": "Only Phase"}],
        }

    def boards(self):
        return self._boards

    def board_columns(self, uuid):
        return self._cols[uuid]


def _pick(text):
    b, c, problem = slack_app.resolve_board_phase(FakeClient(), text)
    return (b and b["title"], c and c["title"], problem)


def test_board_and_phase():
    assert _pick("Acquisitions / Make Offer")[:2] == ("Acquisitions", "Make Offer")
    assert _pick("acquisitions make offer")[:2] == ("Acquisitions", "Make Offer")


def test_longest_board_wins_and_dash_style_does_not_matter():
    assert _pick("Raw – Canyon / Follow Up")[:2] == ("Raw - Canyon", "Follow Up")
    assert _pick("Raw / New Lead")[:2] == ("Raw", "New Lead")


def test_board_without_phase_lists_its_phases():
    board, col, problem = _pick("Raw - Canyon")
    assert board is None and col is None
    assert "Raw - Canyon" in problem and "New Lead, Follow Up" in problem
    assert "Nothing was moved" in problem


def test_siftline_link_names_the_board():
    link = f"https://app.reisift.io/siftline/{RAW}"
    assert _pick(f"{link} Follow Up")[:2] == ("Raw - Canyon", "Follow Up")
    assert "New Lead, Follow Up" in _pick(link)[2]
    assert "isn't a board" in _pick("https://app.reisift.io/siftline/" + "0" * 8 + "-0000-0000-0000-" + "0" * 12)[2]


def test_single_phase_board_needs_no_phase():
    assert _pick("Solo Board")[:2] == ("Solo Board", "Only Phase")


def test_phase_alone_only_when_unambiguous():
    assert _pick("make offer")[:2] == ("Acquisitions", "Make Offer")
    board, _, problem = _pick("follow up")
    assert board is None and "more than one board" in problem


def test_unknown_board_lists_boards():
    board, _, problem = _pick("Dispo / Sold")
    assert board is None and "Raw - Canyon" in problem and "Acquisitions" in problem


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} passed")
