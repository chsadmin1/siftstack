"""
siftline_kpi.py - KPI tracker for a DataSift (REISift) SiftLine board.

Combines two different metric families for one board:
  1. Pipeline/stage metrics from the SiftLine board/column/card API: cards per
     column right now, time-in-column (current + lifetime cumulative per the
     timeline endpoint), a reach/conversion funnel across columns, stale-card
     flags, per-assignee workload, and property-status mix.
  2. Calling-activity metrics from the per-property activity log (dials,
     answer/conversation/contact rates, correct/wrong/dead numbers, leads),
     scoped to EXACTLY the property records sitting on this board -- not the
     whole account. Same three-rate framework as the kpi-engine skill.

NOTE ON SCOPE (verified live 2026-08-24): "DEALS X DOOR" is not one board in
this account, it's a family: the bulk board (this script's default), plus
"DEALS X DOOR - DON" and "DEALS X DOOR - NICO" (per-rep splits of the same
attempt cadence) and "DEALS X DOOR - Leads" (where a record lands once it
becomes an actual Cold/Warm/Hot Lead or gets an appointment). This script is
scoped to ONE board per run; pass --board to point at a sibling, or run it
once per board if you want the whole family.

Auth: mints a user JWT from DATASIFT_EMAIL / DATASIFT_PASSWORD (same pattern
as datasift_api_upload.py) because SiftLine and the activity log both live
under /api/internal/, which the Open API key cannot reach.

    python src/siftline_kpi.py --probe                 # verify API shapes first
    python src/siftline_kpi.py --days 7                 # trailing week
    python src/siftline_kpi.py --days 7 --xlsx --detail
    python src/siftline_kpi.py --board <uuid> --days 7  # a sibling board
    python src/siftline_kpi.py --days 1 --slack <webhook-url>

Read-only against DataSift. Standard library only (openpyxl optional, --xlsx).
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://apiv2.reisift.io"
HERE = Path(__file__).resolve().parent.parent  # project root
OUT_DIR = HERE / "output"

DEFAULT_BOARD_UUID = "e65b4f8c-9995-4d02-9666-82f9ace5c6d7"  # "DEALS X DOOR"

DEFAULT_BENCHMARKS = {
    "dials_floor_per_caller": 150,
    "conversation_min_seconds": 60,
    "meaningful_conversation_min_seconds": 120,
    "voicemail_max_seconds": 30,
    "dials_per_correct_scored": 9,
    "dials_per_correct_blind": 32,
    "correct_numbers_per_deal": 100,
    "lead_statuses": ["Cold Lead", "Warm Lead", "Hot Lead", "new_lead", "New Lead",
                      "No Contact New Lead", "Nurture New Lead"],
    "excluded_callers": [],
}

CORRECT_STATES = {"CORRECT", "CORRECT_DNC"}
WRONG_STATES = {"WRONG", "WRONG_DNC"}
CALL_EVENTS = {"owner.call.made", "owner.call.answered", "owner.call.noanswer",
               "owner.call.received", "owner.call.missed"}


def log(msg: str) -> None:
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def env() -> dict:
    """Env vars first (Fly/CI secrets), .env as a local fallback."""
    out = dict(os.environ)
    try:
        with open(".env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out.setdefault(k.strip(), v.strip())
    except OSError:
        pass
    return out


def load_benchmarks() -> dict:
    bench = dict(DEFAULT_BENCHMARKS)
    f = HERE / "siftline_kpi_benchmarks.json"
    if f.exists():
        bench.update(json.loads(f.read_text(encoding="utf-8")))
    return bench


class Api:
    """Mints its own JWT (DATASIFT_EMAIL/PASSWORD), refreshes transparently."""

    def __init__(self):
        e = env()
        try:
            self.email, self.pw = e["DATASIFT_EMAIL"], e["DATASIFT_PASSWORD"]
        except KeyError as exc:
            raise SystemExit(
                f"{exc.args[0]} is not set. This script mints its own JWT from "
                "DATASIFT_EMAIL / DATASIFT_PASSWORD; set them as env vars or in .env."
            ) from None
        self.token = ""
        self.minted = 0.0
        self._mint()

    def _mint(self):
        body = json.dumps({"email": self.email, "password": self.pw}).encode()
        req = urllib.request.Request(BASE + "/api/token/", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as r:
            self.token = json.loads(r.read())["access"]
        self.minted = time.time()

    def call(self, path, method="GET", body=None, params=None, _retry=True):
        if time.time() - self.minted > 1800:
            self._mint()
        url = BASE + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                t = r.read().decode()
                return json.loads(t) if t.strip().startswith(("{", "[")) else {}
        except urllib.error.HTTPError as e:
            if e.code == 401 and _retry:
                self._mint()
                return self.call(path, method, body, params, _retry=False)
            raise RuntimeError("HTTP %s on %s %s: %s"
                               % (e.code, method, path, e.read().decode()[:300])) from None


def paginate(api: Api, path: str) -> list[dict]:
    out, limit, offset = [], 200, 0
    while True:
        r = api.call(path, params={"limit": limit, "offset": offset})
        rows = r.get("results") or r.get("data") or []
        out.extend(rows)
        total = r.get("count", len(out))
        offset += limit
        if offset >= total or not rows or offset > 20000:
            break
    return out


def parse_iso(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        try:
            return datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=datetime.timezone.utc)
        except ValueError:
            return None


def local_dt(ts: str, tz):
    """Activity-log timestamps are naive UTC 'YYYY-MM-DD HH:MM:SS' (no T/Z)."""
    try:
        return (datetime.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=ZoneInfo("UTC")).astimezone(tz))
    except Exception:
        return None


# ---- SiftLine board pull ----

def load_board(api: Api, board_uuid: str) -> tuple[list[dict], list[dict]]:
    r = api.call(f"/api/internal/siftline/board/{board_uuid}/column/")
    columns = sorted(r.get("results") or r.get("data") or [], key=lambda c: c.get("order", 0))
    if not columns:
        sys.exit(f"Board {board_uuid} has no columns -- check the uuid, or this isn't a "
                 "SiftLine board (see /api/internal/siftline/board/ for the real list).")
    log(f"board has {len(columns)} columns: " + ", ".join(c["title"] for c in columns))

    cards = []
    for c in columns:
        rows = paginate(api, f"/api/internal/siftline/board/column/{c['uuid']}/card/")
        for row in rows:
            row["_column_uuid"] = c["uuid"]
            row["_column_title"] = c["title"]
            row["_column_order"] = c.get("order", 0)
        cards.extend(rows)
        log(f"  {c['title']}: {len(rows)} cards")

    log(f"{len(cards)} total cards; fetching per-card timelines...")
    for i, card in enumerate(cards, 1):
        try:
            card["_timeline"] = api.call(
                f"/api/internal/siftline/board/column/{card['_column_uuid']}"
                f"/card/{card['uuid']}/timeline/")
        except Exception:
            card["_timeline"] = {}
        if i % 50 == 0:
            log(f"  {i}/{len(cards)} timelines fetched...")
    return columns, cards


# ---- pipeline / stage metrics ----

def pipeline_metrics(columns: list[dict], cards: list[dict], stale_seconds: float):
    # NOTE ON A REAL API GOTCHA (verified live 2026-08-24): the timeline
    # endpoint's "time" field is a periodically-recomputed cumulative dwell
    # counter, NOT "now minus arrival". A card created hours ago still shows
    # time=null for its current column until some backend refresh cycle
    # populates it -- confirmed on 4 of 5 freshly-created cards in the same
    # column. So "time present" undercounts recent cards, and averaging only
    # over the cards WITH a computed value (rather than treating null as 0 or
    # excluding it silently) is the only honest read. Every average below is
    # reported as "computed for N of TOTAL" for exactly this reason, and the
    # old "reach/conversion funnel" section was dropped: it produced >100%
    # "conversions" because freshness bias differs column to column, not
    # because of real funnel behavior.
    per_col = {c["uuid"]: {"title": c["title"], "order": c.get("order", 0), "count": 0,
                           "cur_time_sum": 0.0, "cur_time_n": 0, "reached": 0,
                           "lifetime_time_sum": 0.0, "stale": 0,
                           "oldest_addr": None, "oldest_days": -1.0}
               for c in columns}
    per_assignee = defaultdict(lambda: {"name": "Unassigned", "cards": 0,
                                        "by_column": Counter(), "hot": 0})
    status_counts = Counter()

    for card in cards:
        cu = card["_column_uuid"]
        pc = per_col[cu]
        pc["count"] += 1
        tl = card.get("_timeline") or {}
        cur = tl.get(cu) or {}
        cur_time = cur.get("time")
        if isinstance(cur_time, (int, float)):
            pc["cur_time_sum"] += cur_time
            pc["cur_time_n"] += 1
            days = cur_time / 86400
            if days > pc["oldest_days"]:
                pc["oldest_days"] = days
                prop = card.get("prop") or {}
                addr = (prop.get("address") or {}).get("street") or prop.get("uuid", "?")
                pc["oldest_addr"] = addr
            if cur_time >= stale_seconds:
                pc["stale"] += 1
        for col_uuid, info in tl.items():
            t = info.get("time")
            if isinstance(t, (int, float)) and col_uuid in per_col:
                per_col[col_uuid]["reached"] += 1
                per_col[col_uuid]["lifetime_time_sum"] += t

        prop = card.get("prop") or {}
        status_counts[prop.get("status") or "(none)"] += 1
        at = prop.get("assigned_to")
        if isinstance(at, dict) and at.get("uuid"):
            key = at["uuid"]
            per_assignee[key]["name"] = (
                f"{at.get('first_name', '')} {at.get('last_name', '')}".strip()
                or at.get("email", key))
        else:
            key = "unassigned"
        a = per_assignee[key]
        a["cards"] += 1
        a["by_column"][card["_column_title"]] += 1
        if (prop.get("hotness") or 0) > 0:
            a["hot"] += 1

    return per_col, per_assignee, status_counts


def window_card_activity(cards: list[dict], day_from: str, day_to: str, tz):
    created, updated = Counter(), Counter()
    lo, hi = datetime.date.fromisoformat(day_from), datetime.date.fromisoformat(day_to)
    for card in cards:
        cd, ud = parse_iso(card.get("created")), parse_iso(card.get("updated"))
        if cd and lo <= cd.astimezone(tz).date() <= hi:
            created[card["_column_title"]] += 1
        if ud and lo <= ud.astimezone(tz).date() <= hi:
            updated[card["_column_title"]] += 1
    return created, updated


# ---- calling-activity, scoped to this board's property set ----

def caller_of(ev):
    call = (ev.get("payload") or {}).get("call") or {}
    if call.get("direction") == "inbound":
        return ("inbound", "Inbound")
    eu = call.get("external_user") or {}
    return (eu.get("email") or "unknown", eu.get("name") or eu.get("email") or "Unknown")


def author_of(ev):
    info = ev.get("author_extra_info") or {}
    email = info.get("email") or ev.get("author") or "system"
    return (email, (f"{info.get('first_name', '')} {info.get('last_name', '')}".strip() or email))


def detect_new_index(events, key):
    by_tgt = defaultdict(list)
    for ev in events:
        obj = (ev.get("payload") or {}).get("owner" if key == "phone" else "property")
        if not isinstance(obj, dict):
            continue
        pair = obj.get("status")
        tgt = obj.get("phone") if key == "phone" else ev.get("resource")
        if isinstance(pair, list) and len(pair) == 2 and tgt:
            by_tgt[tgt].append((ev.get("timestamp", ""), pair))
    old_new = new_old = 0
    for seq in by_tgt.values():
        seq.sort()
        for (_, e1), (_, e2) in zip(seq, seq[1:]):
            if e1[1] == e2[0] and e1[1] != e2[1]:
                old_new += 1
            elif e1[0] == e2[1] and e1[0] != e2[0]:
                new_old += 1
    return 0 if new_old > old_new else 1


def new_status(ev, key, idx):
    pair = ((ev.get("payload") or {}).get("owner" if key == "phone" else "property") or {}).get("status")
    if isinstance(pair, list) and len(pair) == 2:
        return pair[idx]
    return pair[-1] if isinstance(pair, list) and pair else None


def blank():
    return {"dials": 0, "answered": 0, "noanswer": 0, "talk_seconds": 0, "sms_sent": 0,
            "sms_received": 0, "conversations": 0, "meaningful_conversations": 0,
            "correct_numbers": 0, "wrong_numbers": 0, "dead_numbers": 0, "dnc_numbers": 0,
            "leads": 0, "not_interested": 0, "follow_ups": 0, "appointments": 0,
            "days": set()}


def calling_activity(api: Api, prop_uuids: list[str], day_from: str, day_to: str, tz, bench: dict):
    lead_set = {s.lower() for s in bench["lead_statuses"]}
    excluded = {e.lower() for e in bench["excluded_callers"]}
    conv_s = bench["conversation_min_seconds"]
    mean_s = bench["meaningful_conversation_min_seconds"]
    vm_s = bench["voicemail_max_seconds"]

    rec_events = {}
    for i, uuid in enumerate(prop_uuids, 1):
        try:
            r = api.call(f"/api/internal/property/{uuid}/logs/", params={"limit": 250, "offset": 0})
            evs = r.get("results") or r.get("data") or []
        except Exception:
            evs = []
        keep = []
        for e in evs:
            dt = local_dt(e.get("timestamp", ""), tz)
            if dt and day_from <= dt.date().isoformat() <= day_to:
                keep.append((dt, e))
        if keep:
            rec_events[uuid] = keep
        if i % 50 == 0:
            log(f"  activity logs: {i}/{len(prop_uuids)} records scanned...")
    log(f"{len(rec_events)} of {len(prop_uuids)} board records had activity in window")

    all_phone = [e for evs in rec_events.values() for _, e in evs
                 if e.get("event_type") == "owner.phone.status.updated"]
    all_prop = [e for evs in rec_events.values() for _, e in evs
                if e.get("event_type") == "property.status.updated"]
    p_idx = detect_new_index(all_phone, "phone")
    s_idx = detect_new_index(all_prop, "property")

    acct, per, daily = blank(), defaultdict(blank), defaultdict(blank)
    names, phone_final, prop_final, seen = {}, {}, {}, set()

    def bump(email, day, field, amt=1):
        acct[field] += amt
        per[email][field] += amt
        daily[day][field] += amt

    for uuid, evs in rec_events.items():
        for dt, ev in evs:
            et, day = ev.get("event_type"), dt.date().isoformat()
            if et in CALL_EVENTS or et in ("owner.sms.sent", "owner.sms.received"):
                o = (ev.get("payload") or {}).get("call") or (ev.get("payload") or {}).get("sms") or {}
                k = o.get("uuid") or o.get("external_id")
                if k is not None:
                    if (et, k) in seen:
                        continue
                    seen.add((et, k))
            if et in CALL_EVENTS:
                email, name = caller_of(ev)
                names[email] = name
                call = (ev.get("payload") or {}).get("call") or {}
                if et == "owner.call.made":
                    bump(email, day, "dials")
                    per[email]["days"].add(day)
                    acct["days"] = acct.get("days", set())
                elif et == "owner.call.answered":
                    bump(email, day, "answered")
                    dur = int(call.get("duration") or 0)
                    bump(email, day, "talk_seconds", dur)
                    if dur >= mean_s:
                        bump(email, day, "meaningful_conversations")
                        bump(email, day, "conversations")
                    elif dur >= conv_s:
                        bump(email, day, "conversations")
                elif et == "owner.call.noanswer":
                    bump(email, day, "noanswer")
            elif et == "owner.sms.sent":
                eu = ((ev.get("payload") or {}).get("sms") or {}).get("external_user") or {}
                bump(eu.get("email") or author_of(ev)[0], day, "sms_sent")
            elif et == "owner.sms.received":
                acct["sms_received"] += 1
            elif et == "owner.phone.status.updated":
                phone = ((ev.get("payload") or {}).get("owner") or {}).get("phone")
                ns = new_status(ev, "phone", p_idx)
                if phone and ns:
                    prev = phone_final.get(phone)
                    if prev is None or dt >= prev[0]:
                        phone_final[phone] = (dt, ns, author_of(ev)[0])
            elif et == "property.status.updated":
                ns = new_status(ev, "property", s_idx)
                if ns:
                    prev = prop_final.get(uuid)
                    if prev is None or dt >= prev[0]:
                        prop_final[uuid] = (dt, ns, author_of(ev)[0])
            elif et == "task.completed":
                title = (((ev.get("payload") or {}).get("task") or {}).get("title") or "").lower()
                if any(k in title for k in ("appoint", "appt", "meeting", "consult")):
                    bump(author_of(ev)[0], day, "appointments")

    for phone, (dt, ns, email) in phone_final.items():
        day = dt.date().isoformat()
        if ns in CORRECT_STATES:
            bump(email, day, "correct_numbers")
        elif ns in WRONG_STATES:
            bump(email, day, "wrong_numbers")
        elif ns == "DEAD":
            bump(email, day, "dead_numbers")
        elif ns == "DNC":
            bump(email, day, "dnc_numbers")
    for uuid, (dt, ns, email) in prop_final.items():
        day = dt.date().isoformat()
        if ns.lower() in lead_set:
            bump(email, day, "leads")
        elif ns == "not_interested":
            bump(email, day, "not_interested")
        elif ns == "follow_up":
            bump(email, day, "follow_ups")

    for email in list(per):
        if email.lower() in excluded or email in ("inbound", "system", "unknown"):
            c = per.pop(email)
            for k, v in c.items():
                if isinstance(v, int):
                    acct[k] = max(0, acct[k] - v)

    return {"account_totals": acct, "callers": dict(per), "daily": dict(daily), "names": names}


# ---- rendering ----

def fmt_hms(sec):
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def pct(n, d):
    return f"{n / d * 100:.1f}%" if d else "0.0%"


def render_md(board_title, day_from, day_to, per_col, per_assignee, status_counts,
              created, updated, activity, bench) -> str:
    out = [f"# SiftLine KPI Report - {board_title}", f"### {day_from} to {day_to}", ""]

    out += ["## Pipeline snapshot (right now)", "",
            "> Dwell time comes from SiftLine's per-card timeline, which is a periodically "
            "recomputed counter -- a card can sit in a column for hours and still show "
            "`time: null` until the next refresh. Every average below is scoped to cards "
            "with a computed value, stated as \"computed for N of total\"; it is a real "
            "average over those cards, not an estimate for the whole column.",
            "",
            "| Column | Cards | Avg time (computed cards) | Oldest known dwell | Stale |",
            "|---|---|---|---|---|"]
    cols_sorted = sorted(per_col.values(), key=lambda c: c["order"])
    total_cards = sum(c["count"] for c in cols_sorted)
    for c in cols_sorted:
        if c["cur_time_n"]:
            avg_days = c["cur_time_sum"] / c["cur_time_n"] / 86400
            avg_str = f"{avg_days:.1f}d (computed for {c['cur_time_n']} of {c['count']})"
        else:
            avg_str = f"no computed values yet (0 of {c['count']})" if c["count"] else "-"
        oldest = f"{c['oldest_addr']} ({c['oldest_days']:.1f}d)" if c["oldest_days"] >= 0 else "-"
        out.append(f"| {c['title']} | {c['count']} | {avg_str} | {oldest} | {c['stale']} |")
    out.append(f"\n**Total cards on board: {total_cards}**")

    out += ["", "## Cumulative dwell time recorded per column (lower bound)", "",
            "> \"Cards with recorded time\" is a LOWER BOUND on how many cards have ever sat "
            "in that column -- a card with no recorded time may simply not have been "
            "recomputed yet, not necessarily never-visited. Not a funnel/conversion metric; "
            "just where the measurable time has actually accumulated.",
            "",
            "| Column | Cards with recorded time | Cumulative time (all such cards) |",
            "|---|---|---|"]
    for c in cols_sorted:
        out.append(f"| {c['title']} | {c['reached']} | {fmt_hms(c['lifetime_time_sum'])} |")

    out += ["", f"## Window activity ({day_from} to {day_to})", "",
            "| Column | Cards created | Cards touched/moved |",
            "|---|---|---|"]
    for c in cols_sorted:
        out.append(f"| {c['title']} | {created.get(c['title'], 0)} | {updated.get(c['title'], 0)} |")

    out += ["", "## Per-assignee workload", "",
            "| Assignee | Total cards | Hot (score>0) | Column breakdown |",
            "|---|---|---|---|"]
    for a in sorted(per_assignee.values(), key=lambda x: -x["cards"]):
        breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(a["by_column"].items(),
                                                              key=lambda kv: -kv[1]))
        out.append(f"| {a['name']} | {a['cards']} | {a['hot']} | {breakdown} |")

    out += ["", "## Property status mix on this board", "",
            "| Status | Count |", "|---|---|"]
    for status, n in status_counts.most_common():
        out.append(f"| {status} | {n} |")

    a = activity["account_totals"]
    dials = a["dials"]
    out += ["", "## Calling activity, scoped to this board's records", "",
            f"- Dials: {dials}  |  Answered: {a['answered']} ({pct(a['answered'], dials)})",
            f"- Conversations 60s+: {a['conversations']} ({pct(a['conversations'], dials)})  |  "
            f"Meaningful 120s+: {a['meaningful_conversations']}",
            f"- Correct numbers: {a['correct_numbers']} ({pct(a['correct_numbers'], dials)} right-party)"
            f"  |  Wrong {a['wrong_numbers']}  Dead {a['dead_numbers']}  DNC {a['dnc_numbers']}",
            f"- Leads: {a['leads']}  |  Not interested: {a['not_interested']}  |  "
            f"Follow-ups: {a['follow_ups']}  |  Appointments logged: {a['appointments']}",
            f"- Talk time: {fmt_hms(a['talk_seconds'])}  |  Texts: {a['sms_sent']} out / "
            f"{a['sms_received']} in", "", "### By caller", "",
            "| Caller | Days | Dials | Ans% | Convos | Correct | NI | Leads |",
            "|---|---|---|---|---|---|---|---|"]
    for email, c in sorted(activity["callers"].items(), key=lambda kv: -kv[1]["dials"]):
        if not any(c[k] for k in ("dials", "correct_numbers", "leads", "not_interested")):
            continue
        out.append(f"| {activity['names'].get(email, email)} | {len(c['days'])} | {c['dials']} | "
                   f"{pct(c['answered'], c['dials'])} | {c['conversations']} | "
                   f"{c['correct_numbers']} | {c['not_interested']} | {c['leads']} |")

    dpc = f"{dials / a['correct_numbers']:.1f}" if a["correct_numbers"] else "n/a"
    out += ["", "## Funnel pacing", "",
            f"- Dials per correct number: {dpc} (scored target ~{bench['dials_per_correct_scored']}, "
            f"blind ~{bench['dials_per_correct_blind']})",
            f"- Correct numbers toward next deal: {a['correct_numbers']} / "
            f"{bench['correct_numbers_per_deal']}"]
    return "\n".join(out)


def write_xlsx(path, board_title, per_col, per_assignee, status_counts, created, updated,
              activity, cards) -> bool:
    try:
        from openpyxl import Workbook
    except ImportError:
        log("openpyxl not installed; skipping Excel (pip install openpyxl)")
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "Column Snapshot"
    ws.append(["Column", "Cards", "Avg days in column", "Ever reached", "Stale",
              "Cumulative time (s)"])
    for c in sorted(per_col.values(), key=lambda x: x["order"]):
        avg_days = (c["cur_time_sum"] / c["cur_time_n"] / 86400) if c["cur_time_n"] else 0
        ws.append([c["title"], c["count"], round(avg_days, 2), c["reached"], c["stale"],
                  round(c["lifetime_time_sum"])])

    ws2 = wb.create_sheet("Per-Assignee")
    ws2.append(["Assignee", "Total cards", "Hot", "Column breakdown"])
    for a in sorted(per_assignee.values(), key=lambda x: -x["cards"]):
        ws2.append([a["name"], a["cards"], a["hot"],
                   ", ".join(f"{k}: {v}" for k, v in a["by_column"].items())])

    ws3 = wb.create_sheet("Calling Activity")
    ws3.append(["Caller", "Days", "Dials", "Answered", "Convos", "Correct", "NI", "Leads", "Talk"])
    for email, c in sorted(activity["callers"].items(), key=lambda kv: -kv[1]["dials"]):
        ws3.append([activity["names"].get(email, email), len(c["days"]), c["dials"], c["answered"],
                   c["conversations"], c["correct_numbers"], c["not_interested"], c["leads"],
                   fmt_hms(c["talk_seconds"])])

    ws4 = wb.create_sheet("Status Mix")
    ws4.append(["Status", "Count"])
    for status, n in status_counts.most_common():
        ws4.append([status, n])

    wsr = wb.create_sheet("Records Detail")
    wsr.append(["Address", "Column", "Assigned to", "Status", "Hotness", "Created", "Updated"])
    for card in cards:
        prop = card.get("prop") or {}
        at = prop.get("assigned_to") or {}
        name = f"{at.get('first_name', '')} {at.get('last_name', '')}".strip() if at else ""
        wsr.append([(prop.get("address") or {}).get("street", ""), card["_column_title"],
                   name, prop.get("status", ""), prop.get("hotness", ""),
                   card.get("created", ""), card.get("updated", "")])
    wb.save(path)
    return True


def post_slack(board_title, per_col, activity, webhook: str) -> None:
    total = sum(c["count"] for c in per_col.values())
    a = activity["account_totals"]
    text = (f"SiftLine KPI - {board_title}: {total} cards on board, "
            f"{a['dials']} dials, {a['conversations']} conversations, "
            f"{a['correct_numbers']} correct numbers, {a['leads']} leads")
    r = urllib.request.Request(webhook, data=json.dumps({"text": text}).encode(),
                               headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(r, timeout=15):
        pass
    log("Slack digest posted")


def run_probe(api: Api, board_uuid: str) -> int:
    print("=== boards ===")
    print(json.dumps(api.call("/api/internal/siftline/board/", params={"limit": 50, "offset": 0}),
                     indent=2)[:2000])
    print("\n=== columns ===")
    cols = api.call(f"/api/internal/siftline/board/{board_uuid}/column/")
    print(json.dumps(cols, indent=2)[:2000])
    rows = cols.get("results") or []
    if not rows:
        return 0
    col_uuid = rows[0]["uuid"]
    print(f"\n=== cards in column {col_uuid} ({rows[0].get('title')}) ===")
    cards = api.call(f"/api/internal/siftline/board/column/{col_uuid}/card/",
                     params={"limit": 2, "offset": 0})
    print(json.dumps(cards, indent=2)[:3000])
    results = cards.get("results") or []
    if results:
        card_uuid = results[0]["uuid"]
        print(f"\n=== timeline for card {card_uuid} ===")
        tl = api.call(f"/api/internal/siftline/board/column/{col_uuid}/card/{card_uuid}/timeline/")
        print(json.dumps(tl, indent=2)[:2000])
        prop_uuid = (results[0].get("prop") or {}).get("uuid")
        if prop_uuid:
            print(f"\n=== activity log sample for property {prop_uuid} ===")
            logs = api.call(f"/api/internal/property/{prop_uuid}/logs/",
                            params={"limit": 5, "offset": 0})
            print(json.dumps(logs, indent=2)[:2000])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="SiftLine board KPI tracker (pipeline + calling activity)")
    ap.add_argument("--board", default=DEFAULT_BOARD_UUID, help="board uuid (default: DEALS X DOOR)")
    ap.add_argument("--from", dest="day_from")
    ap.add_argument("--to", dest="day_to")
    ap.add_argument("--days", type=int, help="trailing N days ending today")
    ap.add_argument("--tz", default="America/New_York")
    ap.add_argument("--stale-days", type=float, default=3.0,
                    help="flag cards in a column longer than this as stale (default 3)")
    ap.add_argument("--xlsx", action="store_true")
    ap.add_argument("--detail", action="store_true", help="also write a per-card detail CSV")
    ap.add_argument("--slack", metavar="WEBHOOK_URL")
    ap.add_argument("--probe", action="store_true", help="dump raw API shapes and exit")
    args = ap.parse_args()

    api = Api()

    if args.probe:
        return run_probe(api, args.board)

    tz = ZoneInfo(args.tz)
    today = datetime.datetime.now(tz).date()
    if args.days:
        day_from = (today - datetime.timedelta(days=args.days - 1)).isoformat()
        day_to = today.isoformat()
    elif args.day_from:
        day_from, day_to = args.day_from, args.day_to or today.isoformat()
    else:
        day_from = day_to = today.isoformat()

    board_meta = api.call("/api/internal/siftline/board/", params={"limit": 50, "offset": 0})
    board_title = args.board
    for b in board_meta.get("results") or []:
        if b.get("uuid") == args.board:
            board_title = b.get("title", args.board)
            break

    bench = load_benchmarks()
    columns, cards = load_board(api, args.board)
    per_col, per_assignee, status_counts = pipeline_metrics(columns, cards,
                                                             args.stale_days * 86400)
    created, updated = window_card_activity(cards, day_from, day_to, tz)

    prop_uuids = sorted({(c.get("prop") or {}).get("uuid") for c in cards
                         if (c.get("prop") or {}).get("uuid")})
    log(f"pulling calling activity for {len(prop_uuids)} unique properties on this board...")
    activity = calling_activity(api, prop_uuids, day_from, day_to, tz, bench)

    md = render_md(board_title, day_from, day_to, per_col, per_assignee, status_counts,
                   created, updated, activity, bench)
    print("\n" + md)

    OUT_DIR.mkdir(exist_ok=True)
    slug = "".join(ch if ch.isalnum() else "_" for ch in board_title.lower()).strip("_")
    stem = f"siftline_kpi_{slug}_{day_from}_{day_to}"
    (OUT_DIR / f"{stem}.md").write_text(md, encoding="utf-8")
    log(f"wrote {stem}.md")

    if args.detail:
        with open(OUT_DIR / f"{stem}_cards.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["address", "column", "assigned_to", "status", "hotness",
                       "created", "updated"])
            for card in cards:
                prop = card.get("prop") or {}
                at = prop.get("assigned_to") or {}
                name = f"{at.get('first_name', '')} {at.get('last_name', '')}".strip() if at else ""
                w.writerow([(prop.get("address") or {}).get("street", ""), card["_column_title"],
                           name, prop.get("status", ""), prop.get("hotness", ""),
                           card.get("created", ""), card.get("updated", "")])
        log(f"wrote {stem}_cards.csv ({len(cards)} cards)")

    if args.xlsx:
        if write_xlsx(OUT_DIR / f"{stem}.xlsx", board_title, per_col, per_assignee,
                      status_counts, created, updated, activity, cards):
            log(f"wrote {stem}.xlsx")

    if args.slack:
        post_slack(board_title, per_col, activity, args.slack)

    return 0


if __name__ == "__main__":
    sys.exit(main())
