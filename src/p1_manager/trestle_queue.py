"""Autotext leads that still need phone scores go on a "Trestle - MM-DD-YYYY" list.

The texting assistant (src/sms_agent) only texts a number whose OWN phone tag
is Dial First or Dial Second. A record tagged Autotext whose phones were never
scored sits in the Autotext preset and is never texted, with nothing saying
why. Dan, 2026-10-20: put those records on a dated Trestle list and tell the
P1 channel they need to be Trestled to be auto-texted. Dan, 2026-10-21: as
soon as they're tagged, not on a schedule.

So a watcher thread (watch_forever, started by slack_app) sweeps every
POLL_MINUTES during ACTIVE_HOURS. Each sweep is one paged search for the
Autotext tag; only records it has not looked at recently are read. When a
sweep finds leads that need scoring it adds them to today's Trestle list and
posts ONE message to the P1 channel: why they're there plus the cost
preview, approvable in that thread (enrich.post_batch_preview). The list is
marked handled for the scheduled enrichment scan so it is never previewed
twice; the scan itself only looks at Trestle lists (enrich.ONLY_PREFIX), so
other new lists are never scored.

Which records go on the list ("needs scoring"):
  * a record with phones where NO phone carries any dial tier tag, and
  * a record with no phones that has never been skip traced (approving the
    preview starts DataSift's skip trace for it).
Left off, and only counted in the message:
  * phones already scored Dial Third / Dial Fourth / Drop: Trestle already
    answered, and scoring the same number again costs money and changes
    nothing;
  * records skip traced before with no phones found: they need deep
    prospecting, not Trestle.
A record whose tier tag is only on the RECORD (enrichment before 2026-10-20
tagged records, not phones) counts as unscored, because the texting
assistant cannot see a record-level tier.

What a sweep remembers ("seen": uuid -> verdict + date): a record queued for
scoring is not looked at again for REQUEUE_DAYS, so an unapproved batch is
not re-posted; any other verdict is re-checked once a day, which is how a
record that gains a phone or loses its tier is noticed.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import enrich
import state
from datasift_client import ApiError, DataSiftClient

log = logging.getLogger("p1_trestle_queue")

AUTOTEXT_TAG = os.getenv("P1_AUTOTEXT_TAG", "Autotext")
LIST_PREFIX = "Trestle - "
REQUEUE_DAYS = int(os.getenv("P1_TRESTLE_REQUEUE_DAYS", "7"))
RECHECK_DAYS = 1
POLL_MINUTES = float(os.getenv("P1_TRESTLE_POLL_MINUTES", "5"))
ACTIVE_HOURS = os.getenv("P1_TRESTLE_HOURS", "07:00-20:00")
TEXTABLE_TIERS = {"Dial First", "Dial Second"}
TIER_TAGS = {"Dial First", "Dial Second", "Dial Third", "Dial Fourth", "Drop"}
ADD_SLEEP = 0.25  # per record add-lists call, same pacing as dispo_flow

_sweep_lock = threading.Lock()


def _tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("P1_TIMEZONE", "America/New_York"))


def list_title(day: date) -> str:
    return f"{LIST_PREFIX}{day:%m-%d-%Y}"


def _names(items) -> set[str]:
    out = set()
    for x in items or []:
        name = (x.get("name") or x.get("title") or x.get("tag") or "") if isinstance(x, dict) else str(x)
        if name.strip():
            out.add(name.strip())
    return out


def _rows(r) -> list[dict]:
    return (r.get("results") or r.get("data") or []) if isinstance(r, dict) else (r or [])


def tag_uuid(client: DataSiftClient, title: str) -> str:
    """The record tag's uuid, matched on title ignoring case; "" if missing."""
    want = title.strip().lower()
    for t in _rows(client.api("/api/internal/tag/?limit=999")):
        if (t.get("title") or t.get("name") or "").strip().lower() == want:
            return t.get("uuid") or ""
    return ""


def classify(rec: dict) -> str:
    """textable | low_tier | needs_scoring | no_phone, from one full record."""
    owners = rec.get("owners") or ([rec["owner"]] if isinstance(rec.get("owner"), dict) else [])
    phones = [p for o in owners for p in (o.get("phones") or []) if isinstance(p, dict)]
    phone_tiers = set()
    for p in phones:
        phone_tiers |= _names(p.get("tags")) & TIER_TAGS
    if phone_tiers & TEXTABLE_TIERS:
        return "textable"
    if phone_tiers:
        return "low_tier"
    if phones:
        return "needs_scoring"
    return "no_phone" if rec.get("skiptraced") else "needs_scoring"


def _classify_many(client: DataSiftClient, uuids: list[str]) -> dict[str, str]:
    def _one(u):
        try:
            return u, classify(client.api(f"/api/internal/property/{u}/"))
        except ApiError as e:
            log.warning("could not read record %s: %s", u, str(e)[:120])
            return u, "read_error"

    with ThreadPoolExecutor(max_workers=4) as ex:
        return dict(ex.map(_one, uuids))


def _ensure_list(client: DataSiftClient, title: str) -> str:
    for x in _rows(client.api("/api/internal/list/?limit=999")):
        if (x.get("title") or "").strip() == title:
            return x.get("uuid") or ""
    return (client.api("/api/internal/list/", "POST", {"title": title}) or {}).get("uuid", "")


def _add_to_list(client: DataSiftClient, uuids: list[str], title: str) -> tuple[list[str], int]:
    added, failed = [], 0
    for u in uuids:
        try:
            # STRING, not an array: an array 201s and does nothing (dispo_flow.py).
            client.api(f"/api/internal/property/{u}/add-lists/", "POST", {"lists": title})
            added.append(u)
        except ApiError as e:
            failed += 1
            log.warning("add-lists failed for %s: %s", u, str(e)[:120])
        time.sleep(ADD_SLEEP)
    return added, failed


def header_text(title: str, added: int, counts: dict) -> str:
    lines = [
        f":iphone: *Auto-texting needs phone scores*: {added} lead(s) tagged {AUTOTEXT_TAG} "
        "have no Dial First or Dial Second number yet, so the texting assistant can't text them.",
        f"They're on the DataSift list *{title}* and need to be Trestled to take advantage of "
        "auto-texting.",
    ]
    extra = []
    if counts.get("low_tier"):
        extra.append(f"{counts['low_tier']} newly tagged were already scored Dial Third or "
                     "lower, so they won't be texted and weren't added")
    if counts.get("no_phone"):
        extra.append(f"{counts['no_phone']} have no phone even after a skip trace and need "
                     "deep prospecting")
    if counts.get("read_error"):
        extra.append(f"{counts['read_error']} records couldn't be read and will be retried")
    if counts.get("add_failed"):
        extra.append(f"{counts['add_failed']} couldn't be added to the list and will be retried")
    if extra:
        lines.append("_" + "; ".join(extra) + "._")
    return "\n".join(lines)


def _due(entry: dict | None, today: date) -> bool:
    if not entry:
        return True
    try:
        last = date.fromisoformat(entry.get("d", ""))
    except ValueError:
        return True
    wait = REQUEUE_DAYS if entry.get("v") == "needs_scoring" else RECHECK_DAYS
    return (today - last).days >= wait


def sweep(post, client: DataSiftClient | None = None, today: date | None = None,
          commit: bool = True) -> dict:
    """One pass: queue and announce Autotext leads that need scoring.

    `post(text) -> ts`. Posts only when something was queued, or once a day
    when the Autotext tag can't be found."""
    with _sweep_lock:
        return _sweep(post, client, today or datetime.now(_tz()).date(), commit)


def _sweep(post, client, today: date, commit: bool) -> dict:
    st = state.load_trestle_state()
    client = client or DataSiftClient.from_env()
    uuid = tag_uuid(client, AUTOTEXT_TAG)
    if not uuid:
        if commit and st.get("warned") != today.isoformat():
            st["warned"] = today.isoformat()
            state.save_trestle_state(st)
            post(f":warning: Couldn't find a DataSift tag named *{AUTOTEXT_TAG}*, so Autotext "
                 "leads can't be checked for phone scores.")
        return {"error": f"tag {AUTOTEXT_TAG!r} not found"}

    records = [r.get("uuid") or r.get("saved_uuid")
               for r in client.search_properties({"any_tags": [uuid]})
               if r.get("uuid") or r.get("saved_uuid")]
    seen = st.get("seen", {})
    check = [u for u in records if _due(seen.get(u), today)]
    if not check:
        return {"autotext": len(records), "checked": 0}

    first_look = {u for u in check if u not in seen}
    verdicts = _classify_many(client, check)
    counts: dict[str, int] = {}
    for u, v in verdicts.items():
        # Low tier / no phone are only worth saying once, for newly tagged
        # leads; read errors are said whenever they happen.
        if v in ("low_tier", "no_phone") and u not in first_look:
            continue
        counts[v] = counts.get(v, 0) + 1
    need = [u for u, v in verdicts.items() if v == "needs_scoring"]
    title = list_title(today)
    result = {"autotext": len(records), "checked": len(check), "list": title,
              "to_add": len(need), **counts}
    if not commit:
        return result

    stamp = today.isoformat()
    for u, v in verdicts.items():
        if v not in ("read_error", "needs_scoring"):
            seen[u] = {"v": v, "d": stamp}
    st["seen"] = seen
    if not need:
        state.save_trestle_state(st)
        return result

    list_uuid = _ensure_list(client, title)
    if not list_uuid:
        state.save_trestle_state(st)
        log.warning("could not create list %s; %d leads retried next sweep", title, len(need))
        return {**result, "error": "list not created"}
    # Before anything else can look: the scheduled scan must not preview it too.
    enrich.mark_handled(list_uuid, title, "trestle_queue")

    added, failed = _add_to_list(client, need, title)
    for u in added:
        seen[u] = {"v": "needs_scoring", "d": stamp}
    state.save_trestle_state(st)
    if failed:
        counts["add_failed"] = failed
    if not added:
        return {**result, "added": 0, "failed": failed}

    header = header_text(title, len(added), counts)
    ts = enrich.post_batch_preview(post, client, added, header, list_uuid=list_uuid, title=title)
    if not ts:
        post(header)  # nothing scoreable after all, or the preview post failed
    return {**result, "added": len(added), "failed": failed, "ts": ts}


def _active(now: datetime) -> bool:
    try:
        start, end = (datetime.strptime(x.strip(), "%H:%M").time()
                      for x in ACTIVE_HOURS.split("-"))
    except ValueError:
        return True
    return start <= now.time() < end


def watch_forever(post) -> None:
    """The watcher thread: sweep every POLL_MINUTES during ACTIVE_HOURS."""
    log.info("Trestle watcher up: every %s min, %s %s", POLL_MINUTES, ACTIVE_HOURS,
             os.getenv("P1_TIMEZONE", "America/New_York"))
    while True:
        if _active(datetime.now(_tz())):
            try:
                result = sweep(post)
                if result.get("added") or result.get("error"):
                    log.info("Trestle sweep: %s", result)
            except Exception:
                log.exception("Trestle sweep raised, watcher continuing")
        time.sleep(POLL_MINUTES * 60)


def start_watcher(post) -> threading.Thread:
    t = threading.Thread(target=watch_forever, args=(post,), daemon=True,
                         name="p1-trestle-watcher")
    t.start()
    return t
