"""Phone enrichment for new DataSift lists, approved from Slack.

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
     dial tier tag, and count the phone numbers left to score. Post the
     cost estimate to the P1 channel and remember the batch under the
     message's ts. Nothing is charged.
  2. APPROVE: a reply of "approve" in that thread scores every phone with
     Trestle, tags each record with its best tier (Dial First .. Drop) using
     the safe bulk tag call, checks the quality gate and posts the result in
     the thread. "skip" marks the lists handled without spending anything.

Records with no phone numbers are counted, not scored: they need a DataSift
skip trace first. Run it on the list in DataSift and they are picked up the
next time that list is sent through /trigger/enrich.

Tier tags go on the RECORD (its best phone), matching what the old job
wrote and what the team filters on. A phone whose Trestle call fails gets
no score, and a record whose phones all failed gets no tag, rather than a
misleading "Drop".
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

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

# The quality gate from the client deliverable: a list is worth dialing when
# most records have a phone and enough of them have a top-tier number.
GATE_PHONE_COVERAGE = 0.70
GATE_TOP_TIER_MIN = 18

APPROVE_WORDS = ("approve", "approved", "yes", "go", "run it")
SKIP_WORDS = ("skip", "no", "cancel", "don't", "dont")

_run_lock = threading.Lock()


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


def record_phones(client: DataSiftClient, uuid: str) -> tuple[list[str], set[str]]:
    """(phones, tag names) for one record. The list search returns phones
    empty, so each record is read on its own, as the old d9 job found."""
    rec = client.api(f"/api/internal/property/{uuid}/")
    owners = rec.get("owners") or ([rec["owner"]] if isinstance(rec.get("owner"), dict) else [])
    phones: list[str] = []
    for owner in owners:
        for p in owner.get("phones") or []:
            num = _digits(p.get("phone") or p.get("number") if isinstance(p, dict) else p)
            if num and num not in phones:
                phones.append(num)
    return phones, _names(rec.get("tags"))


def collect(client: DataSiftClient, lists: list[dict]) -> dict:
    """Every record on `lists` that still needs scoring, deduped across lists."""
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
    already, no_phone, errors = 0, 0, 0

    def _one(u):
        try:
            return u, record_phones(client, u)
        except ApiError as e:
            log.warning("could not read record %s: %s", u, str(e)[:120])
            return u, None

    with ThreadPoolExecutor(max_workers=4) as ex:
        for u, got in ex.map(_one, list(uuids)):
            if got is None:
                errors += 1
                continue
            phones, tags = got
            if tags & TIER_TAGS:
                already += 1
            elif not phones:
                no_phone += 1
            else:
                records[u] = phones

    unique_phones = {p for ps in records.values() for p in ps}
    return {
        "records": records,
        "list_sizes": per_list,
        "total_records": len(uuids),
        "already_tiered": already,
        "no_phone": no_phone,
        "read_errors": errors,
        "phones": len(unique_phones),
        "est_cost": round(len(unique_phones) * PRICE_PER_PHONE, 2),
    }


def new_lists(client: DataSiftClient) -> tuple[list[dict], bool]:
    """Lists not yet handled. On the very first run every existing list is
    recorded as a baseline, so turning this on never bills the whole
    account's history; returns (lists, seeded_baseline)."""
    st = state.load_enrich_state()
    lists = all_lists(client)
    if not st.get("baseline_set"):
        st["baseline_set"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for u in list_uuids:
        handled[u] = {"title": titles.get(u, ""), "status": status, "at": stamp}
    state.save_enrich_state(st)


# ---------------------------------------------------------------- preview

def preview_text(lists: list[dict], c: dict) -> str:
    lines = [f":telephone_receiver: *Phone enrichment preview*: {len(lists)} new list(s)"]
    for lst in lists:
        lines.append(f"  - {lst.get('title')}: {c['list_sizes'].get(lst['uuid'], 0)} records")
    lines.append(
        f"\n*{len(c['records'])} records* have phones to score: *{c['phones']} unique numbers*, "
        f"estimated *${c['est_cost']:.2f}* in Trestle charges (${PRICE_PER_PHONE:.3f} per number).")
    if c["already_tiered"]:
        lines.append(f"{c['already_tiered']} records already have a dial tier and are skipped.")
    if c["no_phone"]:
        lines.append(f":warning: {c['no_phone']} records have no phone numbers. Skip trace them in "
                     "DataSift first; they are not part of this charge.")
    if c["read_errors"]:
        lines.append(f"{c['read_errors']} records could not be read and are left out.")
    if c["records"]:
        lines.append("\nReply *approve* in this thread to score and tag them, or *skip* to "
                     "pass on these lists. Nothing is charged until someone approves.")
    return "\n".join(lines)


def run_preview(post, client: DataSiftClient | None = None,
                only_titles: list[str] | None = None) -> dict:
    """The scheduled step. `post(text) -> ts or None` sends to the P1 channel.

    only_titles: preview exactly these lists (by title), whether or not they
    were handled before. The manual way to re-run a list after a skip trace."""
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
    if not c["records"]:
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
        "records": c["records"], "phones": c["phones"], "est_cost": c["est_cost"],
        "no_phone": c["no_phone"], "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return {"new_lists": len(lists), "phones": c["phones"], "est_cost": c["est_cost"], "ts": ts}


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
    return tiers, {"scored": len(unique) - failed, "failed": failed}


def gate(tiers: dict[str, str], total_with_phones: int, no_phone: int) -> dict:
    total = total_with_phones + no_phone
    coverage = total_with_phones / total if total else 0.0
    top = sum(1 for t in tiers.values() if t in ("Dial First", "Dial Second"))
    return {"coverage": coverage, "top_tier": top,
            "passed": coverage >= GATE_PHONE_COVERAGE and top >= GATE_TOP_TIER_MIN}


def run_enrichment(pending: dict, client: DataSiftClient | None = None) -> str:
    """Score, tag, gate. Returns the Slack summary."""
    api_key = os.environ.get("TRESTLE_API_KEY", "").strip()
    if not api_key:
        return ":warning: TRESTLE_API_KEY is not set on the P1 Manager, nothing was scored."
    client = client or DataSiftClient.from_env()
    records = pending["records"]
    tiers, stats = score_records(records, api_key)

    by_tier: dict[str, list[str]] = {}
    for uuid, tier in tiers.items():
        by_tier.setdefault(tier, []).append(uuid)
    for tier, uuids in by_tier.items():
        client.bulk_add_tags(uuids, [tier])

    g = gate(tiers, len(records), int(pending.get("no_phone", 0)))
    dist = " | ".join(f"{t}: {len(by_tier.get(t, []))}" for t, _, _ in TIERS)
    cost = stats["scored"] * PRICE_PER_PHONE
    verdict = (":white_check_mark: *Quality gate passed*" if g["passed"]
               else ":warning: *Quality gate not met*")
    lines = [
        f":telephone_receiver: Scored {stats['scored']} numbers (~${cost:.2f}) and tagged "
        f"{len(tiers)} records.",
        dist,
        f"{verdict}: {g['coverage'] * 100:.0f}% of records have phones (need 70%), "
        f"{g['top_tier']} have a Dial First or Second number (need {GATE_TOP_TIER_MIN}).",
    ]
    if stats["failed"]:
        lines.append(f"{stats['failed']} numbers could not be scored and were left untagged.")
    if not g["passed"]:
        lines.append("Expect a thin dial session from these lists: consider a skip trace or "
                     "deep prospecting on the records without a strong number.")
    return "\n".join(lines)


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
        reply("Reply *approve* to run the Trestle scoring, or *skip* to pass.")
        return

    with _run_lock:  # one approval runs at a time; a double reply is a no-op
        current = state.load_pending_asks().get(thread_ts) or {}
        if current.get("status") != "pending":
            return
        pending["status"] = "running"
        state.save_pending_ask(thread_ts, pending)

    reply(f":hourglass_flowing_sand: Approved. Scoring {pending.get('phones', 0)} numbers "
          f"(~${pending.get('est_cost', 0):.2f}). Results will post here.")

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

    threading.Thread(target=_work, daemon=True, name="p1-enrich-run").start()
