"""Phone enrichment for new DataSift lists: skip trace + Trestle, approved from Slack.

Replaces the GitHub Actions auto-enrich job that lived in the old
dan525homes/creativehomesolutions repo (auto-enrich.yml +
auto_enrich_new_lists.py). That job could not run as committed (it imported
d9_verify_enrichment, which was never checked in), kept its "already done"
list in a file the Actions runner throws away, scored fake numbers when
DataSift errored, and could only be approved by clicking through GitHub.
Here it lives inside the always-on P1 Manager, which already has a volume,
DataSift credentials and a Slack listener:

  1. PREVIEW (weekdays, P1_ENRICH_AT, default 07:00 and 12:00 Mountain):
     find lists created since the watcher started that have not been
     handled, read every record on them, skip records that already carry a
     dial tier tag, count the phone numbers left to score and the records
     that have never been skip traced. Post the estimate to the P1 channel
     and remember the batch under the message's ts. Nothing is charged.
  2. APPROVE (a reply of "approve" in that thread):
       a. start a DataSift skip trace if any record has never been traced
          (see SKIP TRACE below),
       b. score every phone already on the records with Trestle and tag each
          record with its best tier (Dial First .. Drop) via the safe bulk
          tag call,
       c. keep checking the skip-traced records; as their numbers land, score
          and tag those too, and post a follow-up in the thread. Leftovers
          are re-checked at each scheduled run for SKIPTRACE_GIVE_UP_HOURS.
     "skip" marks the lists handled without spending anything.

SKIP TRACE: POST /api/internal/property/skip-trace/ IGNORES which records it
is given and traces every untraced record in the account (measured live
2026-08-19, see src/datasift_skiptrace.py: one uuid in, 53,322 records
traced). That costs nothing on DataSift's unlimited skip trace plan, which
CHS is on (stats read 2026-10-14: value_spent $0.00, saved $511.20), and
would bill the whole account on a metered plan. So start_skip_trace() reads
the stats first and refuses unless value_spent is 0 and the unlimited
savings counter is positive; the preview tells the team it is account-wide.

Tier tags go on the RECORD (its best phone), matching what the old job
wrote and what the team filters on, AND on each scored PHONE (its own tier,
2026-10-20). The texting assistant (src/sms_agent) only texts a number whose
own phone tag is Dial First or Second; with the tier on the record alone, a
Trestled lead was never texted. Phone tags are written through the owner's
upsert-phones call, the same one sms_agent/crm.py uses for phone status: it
REPLACES the phone object, so the whole object is re-sent with only the tier
tags changed, then read back. A record counts as already scored only when a
phone carries a tier, so records tiered before this change are rescored once.
A phone whose Trestle call fails gets no score, and a record whose phones
all failed gets no tag, rather than a misleading "Drop".
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

import state
from datasift_client import ApiError, DataSiftClient

log = logging.getLogger("p1_enrich")

TIERS = [  # best first; (tag, low, high) on Trestle's 0-100 activity score
    ("Dial First", 81, 100),
    ("Dial Second", 61, 80),
    ("Dial Third", 41, 60),
    ("Dial Fourth", 21, 40),
    ("Drop", 0, 20),
]
TIER_TAGS = {t for t, _, _ in TIERS}
TRESTLE_URL = "https://api.trestleiq.com/3.0/phone_intel"
PRICE_PER_PHONE = float(os.getenv("TRESTLE_PRICE_PER_PHONE", "0.015"))
# For the preview only: how many numbers a skip trace hit usually returns.
EST_PHONES_PER_TRACE = float(os.getenv("P1_EST_PHONES_PER_TRACE", "3"))

SKIPTRACE_STATS = "/api/internal/activity/skiptrace/stats/"
SKIPTRACE_RUN = "/api/internal/property/skip-trace/"
SKIPTRACE_POLL_MINUTES = int(os.getenv("P1_SKIPTRACE_POLL_MINUTES", "10"))
SKIPTRACE_WATCH_MINUTES = int(os.getenv("P1_SKIPTRACE_WATCH_MINUTES", "120"))
SKIPTRACE_GIVE_UP_HOURS = int(os.getenv("P1_SKIPTRACE_GIVE_UP_HOURS", "48"))

# The quality gate from the client deliverable: a list is worth dialing when
# most records have a phone and enough of them have a top-tier number.
GATE_PHONE_COVERAGE = 0.70
GATE_TOP_TIER_MIN = 18

APPROVE_WORDS = ("approve", "approved", "yes", "go", "run it")
SKIP_WORDS = ("skip", "no", "cancel", "don't", "dont")

_run_lock = threading.Lock()
# The post-approval watcher and the scheduled follow-up both score skip trace
# results; one lock plus a fresh read of the batch inside it means a record's
# numbers are never paid for twice.
_trace_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- reading

def _names(items) -> set[str]:
    """Tags/lists come back as strings or as {name|title} dicts depending on
    the endpoint; compare on the name either way."""
    out = set()
    for x in items or []:
        name = (x.get("name") or x.get("title") or "") if isinstance(x, dict) else str(x)
        if name.strip():
            out.add(name.strip())
    return out


def _digits(raw) -> str:
    d = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else ""


def all_lists(client: DataSiftClient) -> list[dict]:
    r = client.api("/api/internal/list/?limit=999")
    rows = (r.get("results") or r.get("data") or []) if isinstance(r, dict) else (r or [])
    return [x for x in rows if x.get("uuid")]


def _owners(rec: dict) -> list[dict]:
    return rec.get("owners") or ([rec["owner"]] if isinstance(rec.get("owner"), dict) else [])


def _phone_number(p) -> str:
    return _digits(p.get("phone") or p.get("number") if isinstance(p, dict) else p)


def read_record(client: DataSiftClient, uuid: str) -> dict:
    """{phones, tags, phone_tiers, skiptraced} for one record. The list search
    returns phones empty, so each record is read on its own, as the old d9 job
    found. phone_tiers = the dial tier tags carried by its phones."""
    rec = client.api(f"/api/internal/property/{uuid}/")
    phones: list[str] = []
    phone_tiers: set[str] = set()
    for owner in _owners(rec):
        for p in owner.get("phones") or []:
            num = _phone_number(p)
            if num and num not in phones:
                phones.append(num)
            if isinstance(p, dict):
                phone_tiers |= _names(p.get("tags")) & TIER_TAGS
    return {"phones": phones, "tags": _names(rec.get("tags")), "phone_tiers": phone_tiers,
            "skiptraced": bool(rec.get("skiptraced"))}


def _read_many(client: DataSiftClient, uuids: list[str]) -> dict[str, dict | None]:
    def _one(u):
        try:
            return u, read_record(client, u)
        except ApiError as e:
            log.warning("could not read record %s: %s", u, str(e)[:120])
            return u, None

    with ThreadPoolExecutor(max_workers=4) as ex:
        return dict(ex.map(_one, uuids))


def collect(client: DataSiftClient, lists: list[dict]) -> dict:
    """Every record on `lists` that still needs work, deduped across lists."""
    uuids: dict[str, str] = {}  # record uuid -> first list title it was seen on
    per_list: dict[str, int] = {}
    for lst in lists:
        rows = client.search_properties({"any_lists": [lst["uuid"]]})
        per_list[lst["uuid"]] = len(rows)
        for row in rows:
            u = row.get("uuid") or row.get("saved_uuid")
            if u:
                uuids.setdefault(u, lst.get("title", ""))

    records: dict[str, list[str]] = {}
    needs_trace: list[str] = []
    already, no_hit, errors = 0, 0, 0
    for u, got in _read_many(client, list(uuids)).items():
        if got is None:
            errors += 1
        elif got["phone_tiers"]:
            already += 1  # a record-level tier alone is not enough; see module doc
        elif got["phones"]:
            records[u] = got["phones"]
        elif got["skiptraced"]:
            no_hit += 1  # traced before and found nothing; tracing again won't help
        else:
            needs_trace.append(u)

    unique_phones = {p for ps in records.values() for p in ps}
    return {
        "records": records,
        "needs_trace": needs_trace,
        "list_sizes": per_list,
        "total_records": len(uuids),
        "already_tiered": already,
        "no_hit": no_hit,
        "read_errors": errors,
        "phones": len(unique_phones),
        "est_cost": round(len(unique_phones) * PRICE_PER_PHONE, 2),
        "est_trace_cost": round(len(needs_trace) * EST_PHONES_PER_TRACE * PRICE_PER_PHONE, 2),
    }


def new_lists(client: DataSiftClient) -> tuple[list[dict], bool]:
    """Lists not yet handled. On the very first run every existing list is
    recorded as a baseline, so turning this on never bills the whole
    account's history; returns (lists, seeded_baseline)."""
    st = state.load_enrich_state()
    lists = all_lists(client)
    if not st.get("baseline_set"):
        st["baseline_set"] = _now()
        st.setdefault("handled", {})
        for lst in lists:
            st["handled"][lst["uuid"]] = {"title": lst.get("title"), "status": "baseline"}
        state.save_enrich_state(st)
        return [], True
    handled = st.get("handled", {})
    in_flight = {u for ask in state.load_pending_asks().values()
                 if ask.get("kind") == "enrich" and ask.get("status") == "pending"
                 for u in ask.get("list_uuids", [])}
    return [x for x in lists if x["uuid"] not in handled and x["uuid"] not in in_flight], False


def _mark(list_uuids: list[str], titles: dict[str, str], status: str) -> None:
    st = state.load_enrich_state()
    handled = st.setdefault("handled", {})
    for u in list_uuids:
        handled[u] = {"title": titles.get(u, ""), "status": status, "at": _now()}
    state.save_enrich_state(st)


# ---------------------------------------------------------------- preview

def preview_text(lists: list[dict], c: dict) -> str:
    lines = [f":telephone_receiver: *Phone enrichment preview*: {len(lists)} new list(s)"]
    for lst in lists:
        lines.append(f"  - {lst.get('title')}: {c['list_sizes'].get(lst['uuid'], 0)} records")
    lines.append(
        f"\n*{len(c['records'])} records* have phones to score: *{c['phones']} unique numbers*, "
        f"estimated *${c['est_cost']:.2f}* in Trestle charges (${PRICE_PER_PHONE:.3f} per number).")
    if c["needs_trace"]:
        lines.append(
            f":mag: *{len(c['needs_trace'])} records have never been skip traced.* Approving also "
            "starts a DataSift skip trace (free on your unlimited plan; DataSift runs it on every "
            "untraced record in the account, not just these lists). Their new numbers are scored "
            f"as they arrive, roughly *${c['est_trace_cost']:.2f}* more.")
    if c["already_tiered"]:
        lines.append(f"{c['already_tiered']} records already have a dial tier and are skipped.")
    if c["no_hit"]:
        lines.append(f"{c['no_hit']} records were skip traced before with no phones found; "
                     "they need deep prospecting rather than another trace.")
    if c["read_errors"]:
        lines.append(f"{c['read_errors']} records could not be read and are left out.")
    if c["records"] or c["needs_trace"]:
        lines.append("\nReply *approve* in this thread to run it, or *skip* to pass on these "
                     "lists. Nothing is charged or traced until someone approves.")
    return "\n".join(lines)


def run_preview(post, client: DataSiftClient | None = None,
                only_titles: list[str] | None = None) -> dict:
    """The scheduled step. `post(text) -> ts or None` sends to the P1 channel.

    only_titles: preview exactly these lists (by title), whether or not they
    were handled before. The manual way to re-run a list."""
    client = client or DataSiftClient.from_env()
    if only_titles:
        wanted = {t.strip().lower() for t in only_titles}
        lists, seeded = [x for x in all_lists(client)
                         if (x.get("title") or "").strip().lower() in wanted], False
        if not lists:
            post(f":warning: No DataSift list matched {', '.join(only_titles)}.")
            return {"new_lists": 0}
    else:
        lists, seeded = new_lists(client)
    if seeded:
        n = len(state.load_enrich_state().get("handled", {}))
        post(f":information_source: Phone enrichment watcher is on. The {n} lists already in "
             "DataSift are treated as handled; lists created from now on get a cost preview here "
             "at 7 AM and 12 PM on weekdays.")
        return {"seeded": n}
    if not lists:
        log.info("enrich preview: no new lists")
        return {"new_lists": 0}

    c = collect(client, lists)
    titles = {x["uuid"]: x.get("title", "") for x in lists}
    if not c["records"] and not c["needs_trace"]:
        post(preview_text(lists, c))
        _mark(list(titles), titles, "nothing_to_score")
        return {"new_lists": len(lists), "phones": 0}

    ts = post(preview_text(lists, c))
    if not ts:
        log.warning("enrich preview: Slack post failed, lists stay unhandled for the next run")
        return {"new_lists": len(lists), "posted": False}
    state.save_pending_ask(ts, {
        "kind": "enrich", "status": "pending",
        "list_uuids": list(titles), "list_titles": titles,
        "records": c["records"], "needs_trace": c["needs_trace"],
        "phones": c["phones"], "est_cost": c["est_cost"],
        "no_hit": c["no_hit"], "created": _now(),
    })
    return {"new_lists": len(lists), "phones": c["phones"], "est_cost": c["est_cost"],
            "needs_trace": len(c["needs_trace"]), "ts": ts}


# ---------------------------------------------------------------- skip trace

def start_skip_trace(client: DataSiftClient) -> tuple[bool, str]:
    """Trigger DataSift's skip trace, but only on an unlimited plan.

    The endpoint is account-wide no matter what it is sent (module
    docstring), so the plan check is what keeps this from billing every
    record in the account."""
    try:
        before = client.api(SKIPTRACE_STATS)
    except ApiError as e:
        return False, f"could not read skip trace stats ({str(e)[:120]})"
    spent = float(before.get("value_spent") or 0)
    saved = float(before.get("saved_by_unlimited_skiptracing") or 0)
    if spent > 0 or saved <= 0:
        return False, (f"the DataSift plan no longer looks unlimited (spent ${spent:,.2f}, "
                       f"unlimited savings ${saved:,.2f}), and this API would trace and bill "
                       "the whole account. Skip trace these lists from the DataSift app instead.")
    try:
        client.api(SKIPTRACE_RUN, "POST", {})
    except ApiError as e:
        return False, f"DataSift refused the skip trace ({str(e)[:120]})"
    return True, "started"


# ---------------------------------------------------------------- scoring

def trestle_score(phone: str, api_key: str, session: requests.Session) -> int | None:
    for _ in range(3):
        try:
            r = session.get(TRESTLE_URL, params={"phone": phone},
                            headers={"x-api-key": api_key, "Accept": "application/json"}, timeout=20)
        except requests.RequestException:
            continue
        if r.status_code == 200:
            score = r.json().get("activity_score")
            return int(score) if isinstance(score, (int, float)) else None
        if r.status_code in (429, 500, 502, 503):
            continue
        log.warning("Trestle %s for %s: %s", r.status_code, phone[-4:], r.text[:120])
        return None
    return None


def tier_for(score: int) -> str:
    for tag, low, high in TIERS:
        if low <= score <= high:
            return tag
    return "Drop"


def score_records(records: dict[str, list[str]], api_key: str) -> tuple[dict[str, str], dict]:
    """record uuid -> best tier, plus stats. Each unique phone is scored once."""
    unique = sorted({p for ps in records.values() for p in ps})
    session = requests.Session()
    with ThreadPoolExecutor(max_workers=4) as ex:
        scores = dict(zip(unique, ex.map(lambda p: trestle_score(p, api_key, session), unique)))
    tiers: dict[str, str] = {}
    for uuid, phones in records.items():
        got = [scores[p] for p in phones if scores.get(p) is not None]
        if got:
            tiers[uuid] = tier_for(max(got))
    failed = sum(1 for v in scores.values() if v is None)
    phone_tiers = {p: tier_for(v) for p, v in scores.items() if v is not None}
    return tiers, {"scored": len(unique) - failed, "failed": failed, "phone_tiers": phone_tiers}


def _tag(client: DataSiftClient, tiers: dict[str, str]) -> dict[str, int]:
    by_tier: dict[str, list[str]] = {}
    for uuid, tier in tiers.items():
        by_tier.setdefault(tier, []).append(uuid)
    for tier, uuids in by_tier.items():
        client.bulk_add_tags(uuids, [tier])
    return {t: len(by_tier.get(t, [])) for t, _, _ in TIERS}


def _phone_payload(p: dict, tier: str) -> dict:
    """The FULL phone object upsert-phones needs, with its tier tag swapped.
    Mirrors sms_agent/crm.py _phone_payload: anything left out is wiped."""
    tags = sorted(_names(p.get("tags") or []))
    return {
        "number": _phone_number(p),
        "type": (p.get("type") or "UNKNOWN").upper(),
        "tags": [t for t in tags if t not in TIER_TAGS] + [tier],
        "status": (p.get("status") or "UNKNOWN").upper(),
        "is_connected": p.get("is_connected", True),
        "verified": p.get("verified", False),
    }


def _tag_record_phones(client: DataSiftClient, uuid: str,
                       phone_tiers: dict[str, str]) -> tuple[int, int, int]:
    """Write each scored phone's own tier on one record, then read it back.
    Returns (written, verified, failed)."""
    rec = client.api(f"/api/internal/property/{uuid}/")
    wanted: dict[str, str] = {}
    written = failed = 0
    for owner in _owners(rec):
        for p in owner.get("phones") or []:
            num = _phone_number(p) if isinstance(p, dict) else ""
            tier = phone_tiers.get(num)
            if not tier:
                continue
            wanted[num] = tier
            if (_names(p.get("tags")) & TIER_TAGS) == {tier}:
                continue  # already exactly this tier
            try:
                client.api(f"/api/internal/owner/{owner.get('uuid')}/upsert-phones/", "POST",
                           {"phones": [_phone_payload(p, tier)]})
                written += 1
            except ApiError as e:
                failed += 1
                log.warning("phone tag write failed on %s: %s", uuid, str(e)[:120])
    if not wanted:
        return 0, 0, failed
    after = client.api(f"/api/internal/property/{uuid}/")
    verified = sum(1 for o in _owners(after) for p in (o.get("phones") or [])
                   if isinstance(p, dict) and wanted.get(_phone_number(p)) in _names(p.get("tags")))
    return written, verified, failed


def tag_phones(client: DataSiftClient, records: dict[str, list[str]],
               phone_tiers: dict[str, str]) -> dict[str, int]:
    """Put each scored number's own tier on it, across `records`."""
    def _one(u):
        mine = {p: phone_tiers[p] for p in records[u] if p in phone_tiers}
        if not mine:
            return 0, 0, 0, 0
        try:
            return (*_tag_record_phones(client, u, mine), len(mine))
        except ApiError as e:
            log.warning("could not tag phones on %s: %s", u, str(e)[:120])
            return 0, 0, len(mine), len(mine)

    totals = {"written": 0, "verified": 0, "failed": 0, "wanted": 0}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for w, v, f, n in ex.map(_one, list(records)):
            totals["written"] += w
            totals["verified"] += v
            totals["failed"] += f
            totals["wanted"] += n
    return totals


def _phone_tag_line(t: dict) -> str:
    line = (f"Tagged {t['verified']} of {t['wanted']} numbers with their own tier "
            "(what auto-texting reads).")
    if t["verified"] < t["wanted"]:
        line = ":warning: " + line + " The rest did not stick; check the P1 Manager logs."
    return line


def gate(tiers: dict[str, str], total_with_phones: int, no_phone: int) -> dict:
    total = total_with_phones + no_phone
    coverage = total_with_phones / total if total else 0.0
    top = sum(1 for t in tiers.values() if t in ("Dial First", "Dial Second"))
    return {"coverage": coverage, "top_tier": top,
            "passed": coverage >= GATE_PHONE_COVERAGE and top >= GATE_TOP_TIER_MIN}


def _gate_lines(pending: dict) -> list[str]:
    tiers = pending.get("tiers", {})
    with_phones = len(pending.get("records", {}))
    without = len(pending.get("awaiting", [])) + len(pending.get("trace_no_hit", [])) \
        + int(pending.get("no_hit", 0))
    g = gate(tiers, with_phones, without)
    verdict = (":white_check_mark: *Quality gate passed*" if g["passed"]
               else ":warning: *Quality gate not met*")
    lines = [f"{verdict}: {g['coverage'] * 100:.0f}% of records have phones (need 70%), "
             f"{g['top_tier']} have a Dial First or Second number (need {GATE_TOP_TIER_MIN})."]
    if not g["passed"] and not pending.get("awaiting"):
        lines.append("Expect a thin dial session from these lists: consider deep prospecting "
                     "on the records without a strong number.")
    return lines


def run_enrichment(pending: dict, client: DataSiftClient | None = None) -> str:
    """Skip trace (if needed), score, tag, gate. Mutates `pending` with the
    tiers so far and the records still awaiting skip trace results. Returns
    the Slack summary."""
    api_key = os.environ.get("TRESTLE_API_KEY", "").strip()
    if not api_key:
        return ":warning: TRESTLE_API_KEY is not set on the P1 Manager, nothing was scored."
    client = client or DataSiftClient.from_env()
    lines = []

    needs = list(pending.get("needs_trace", []))
    if needs:
        ok, why = start_skip_trace(client)
        if ok:
            pending["awaiting"] = needs
            pending["trace_started"] = _now()
            lines.append(f":mag: Skip trace started for {len(needs)} records. Their numbers are "
                         "scored as results arrive; follow-ups will post here.")
        else:
            pending["trace_no_hit"] = needs
            lines.append(f":warning: Skip trace not started: {why}")

    records = pending.get("records", {})
    tiers, stats = score_records(records, api_key) if records else ({}, {"scored": 0, "failed": 0})
    dist = _tag(client, tiers)
    phone_tags = tag_phones(client, records, stats.get("phone_tiers", {})) if records else None
    pending["tiers"] = tiers
    pending["scored_phones"] = stats["scored"]
    cost = stats["scored"] * PRICE_PER_PHONE
    lines.insert(0, f":telephone_receiver: Scored {stats['scored']} numbers (~${cost:.2f}) and "
                    f"tagged {len(tiers)} records.")
    lines.insert(1, " | ".join(f"{t}: {n}" for t, n in dist.items()))
    if phone_tags and phone_tags["wanted"]:
        lines.insert(2, _phone_tag_line(phone_tags))
    if stats["failed"]:
        lines.append(f"{stats['failed']} numbers could not be scored and were left untagged.")
    lines += _gate_lines(pending)
    return "\n".join(lines)


def check_skip_trace(pending: dict, client: DataSiftClient | None = None,
                     final: bool = False) -> str | None:
    """Score whatever skip trace results have landed for pending['awaiting'].
    Returns a Slack follow-up when something changed (or on the final check),
    else None. Mutates `pending`."""
    awaiting = list(pending.get("awaiting", []))
    if not awaiting:
        return None
    api_key = os.environ.get("TRESTLE_API_KEY", "").strip()
    client = client or DataSiftClient.from_env()
    landed: dict[str, list[str]] = {}
    no_hit: list[str] = []
    still: list[str] = []
    for u, got in _read_many(client, awaiting).items():
        if got is None or not got["skiptraced"]:
            still.append(u)
        elif got["phones"]:
            landed[u] = got["phones"]
        else:
            no_hit.append(u)
    if final:
        no_hit += still
        still = []

    pending["awaiting"] = still
    pending.setdefault("trace_no_hit", []).extend(no_hit)
    if not landed and not no_hit:
        return None

    tiers, stats = score_records(landed, api_key) if landed else ({}, {"scored": 0, "failed": 0})
    dist = _tag(client, tiers)
    phone_tags = tag_phones(client, landed, stats.get("phone_tiers", {})) if landed else None
    pending.setdefault("records", {}).update(landed)
    pending.setdefault("tiers", {}).update(tiers)
    pending["scored_phones"] = int(pending.get("scored_phones", 0)) + stats["scored"]

    lines = [f":mag: Skip trace results: {len(landed)} records came back with numbers"
             + (f", {len(no_hit)} with none" if no_hit else "") + "."]
    if landed:
        lines.append(f"Scored {stats['scored']} more numbers (~${stats['scored'] * PRICE_PER_PHONE:.2f}): "
                     + " | ".join(f"{t}: {n}" for t, n in dist.items() if n))
        if phone_tags and phone_tags["wanted"]:
            lines.append(_phone_tag_line(phone_tags))
    if still:
        lines.append(f"Still waiting on {len(still)} records; checking again later.")
    elif final and no_hit:
        lines.append(f"Stopped waiting after {SKIPTRACE_GIVE_UP_HOURS}h.")
    lines += _gate_lines(pending)
    return "\n".join(lines)


def _expired(pending: dict) -> bool:
    started = pending.get("trace_started")
    if not started:
        return True
    t = datetime.fromisoformat(started)
    return datetime.now(timezone.utc) - t > timedelta(hours=SKIPTRACE_GIVE_UP_HOURS)


def run_followups(reply_in_thread) -> int:
    """Called at each scheduled enrichment slot: finish any approved batch
    still waiting on skip trace results. reply_in_thread(ts, text)."""
    done = 0
    for ts in list(state.load_pending_asks()):
        with _trace_lock:
            pending = state.load_pending_asks().get(ts) or {}
            if pending.get("kind") != "enrich" or not pending.get("awaiting"):
                continue
            try:
                msg = check_skip_trace(pending, final=_expired(pending))
            except Exception:  # noqa: BLE001
                log.exception("skip trace follow-up failed for %s", ts)
                continue
            state.save_pending_ask(ts, pending)
        if msg:
            reply_in_thread(ts, msg)
            done += 1
    return done


def _watch(thread_ts: str, pending: dict, reply) -> None:
    """Right after approval: poll for skip trace results for a while, so the
    team usually sees them the same day. Whatever is left is picked up by
    run_followups at the next scheduled slot."""
    deadline = time.time() + SKIPTRACE_WATCH_MINUTES * 60
    while time.time() < deadline:
        time.sleep(SKIPTRACE_POLL_MINUTES * 60)
        with _trace_lock:
            pending = state.load_pending_asks().get(thread_ts) or {}
            if not pending.get("awaiting"):
                return
            try:
                msg = check_skip_trace(pending)
            except Exception:  # noqa: BLE001
                log.exception("skip trace watch failed")
                continue
            state.save_pending_ask(thread_ts, pending)
        if msg:
            reply(msg)


# ---------------------------------------------------------------- the reply

def handle_reply(thread_ts: str, text: str, pending: dict, reply) -> None:
    """`reply(text)` posts in the thread. Called from slack_app.handle_reply
    for any pending ask whose kind is "enrich"."""
    t = (text or "").strip().lower()
    status = pending.get("status")
    if status != "pending":
        reply(f":information_source: This batch is already {status}.")
        return
    titles = pending.get("list_titles", {})

    if any(t.startswith(w) for w in SKIP_WORDS):
        pending["status"] = "skipped"
        state.save_pending_ask(thread_ts, pending)
        _mark(pending["list_uuids"], titles, "skipped")
        reply(":no_entry_sign: Skipped. Nothing was charged and these lists won't be offered again.")
        return
    if not any(t.startswith(w) for w in APPROVE_WORDS):
        reply("Reply *approve* to run it, or *skip* to pass.")
        return

    with _run_lock:  # one approval runs at a time; a double reply is a no-op
        current = state.load_pending_asks().get(thread_ts) or {}
        if current.get("status") != "pending":
            return
        pending["status"] = "running"
        state.save_pending_ask(thread_ts, pending)

    traced = len(pending.get("needs_trace", []))
    reply(f":hourglass_flowing_sand: Approved. Scoring {pending.get('phones', 0)} numbers "
          f"(~${pending.get('est_cost', 0):.2f})"
          + (f" and starting a skip trace for {traced} records" if traced else "")
          + ". Results will post here.")

    def _work():
        try:
            summary = run_enrichment(pending)
            pending["status"] = "done"
            _mark(pending["list_uuids"], titles, "enriched")
        except Exception as e:  # noqa: BLE001
            log.exception("enrichment run failed")
            summary = f":x: Enrichment failed partway: {str(e)[:200]}. Reply *approve* to retry."
            pending["status"] = "pending"
        state.save_pending_ask(thread_ts, pending)
        reply(summary)
        if pending["status"] == "done" and pending.get("awaiting"):
            _watch(thread_ts, pending, reply)

    threading.Thread(target=_work, daemon=True, name="p1-enrich-run").start()
