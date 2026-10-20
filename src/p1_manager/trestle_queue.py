"""Autotext leads that still need phone scores go on a "Trestle - MM-DD-YYYY" list.

The texting assistant (src/sms_agent) only texts a number whose OWN phone tag
is Dial First or Dial Second. A record tagged Autotext whose phones were never
scored sits in the Autotext preset and is never texted, with nothing saying
why. Dan, 2026-10-20: put those records on a dated Trestle list and tell the
P1 channel they need to be Trestled to be auto-texted.

Runs at the first enrichment slot of each weekday, right BEFORE the
enrichment preview (schedule.run_enrich), so the new list is picked up by
that same preview and its cost estimate posts a few seconds after the notice.
Once a day only: a list created at 7 AM is marked handled by the 7 AM preview,
so records added to it at noon would never be previewed. Leads tagged after
the morning run are caught the next weekday.

Which records go on the list ("needs scoring"):
  * a record with phones where NO phone carries any dial tier tag, and
  * a record with no phones that has never been skip traced (approving the
    enrichment preview starts DataSift's skip trace for it).
Left off, and only counted in the notice:
  * phones already scored Dial Third / Dial Fourth / Drop: Trestle already
    answered, and scoring the same number again costs money and changes
    nothing;
  * records skip traced before with no phones found: they need deep
    prospecting, not Trestle.
A record whose tier tag is only on the RECORD (enrichment before 2026-10-20
tagged records, not phones) counts as unscored, because the texting
assistant cannot see a record-level tier. enrich.collect agrees, so those
records are rescored and their phones tagged.

A record already queued in the last REQUEUE_DAYS is not added again, so an
unapproved list does not grow a new copy every morning.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import state
from datasift_client import ApiError, DataSiftClient

log = logging.getLogger("p1_trestle_queue")

AUTOTEXT_TAG = os.getenv("P1_AUTOTEXT_TAG", "Autotext")
LIST_PREFIX = "Trestle - "
REQUEUE_DAYS = int(os.getenv("P1_TRESTLE_REQUEUE_DAYS", "7"))
TEXTABLE_TIERS = {"Dial First", "Dial Second"}
TIER_TAGS = {"Dial First", "Dial Second", "Dial Third", "Dial Fourth", "Drop"}
ADD_SLEEP = 0.25  # per record add-lists call, same pacing as dispo_flow


def _today() -> date:
    tz = ZoneInfo(os.getenv("P1_TIMEZONE", "America/New_York"))
    return datetime.now(tz).date()


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


def notice_text(title: str, added: int, counts: dict) -> str:
    lines = [
        f":iphone: *Auto-texting needs phone scores*: {added} lead(s) tagged {AUTOTEXT_TAG} "
        "have no Dial First or Dial Second number yet, so the texting assistant can't text them.",
        f"They're on the DataSift list *{title}*. They need to be Trestled to take advantage of "
        "auto-texting: reply *approve* to the phone enrichment preview for that list, which "
        "posts here next.",
    ]
    extra = []
    if counts.get("low_tier"):
        extra.append(f"{counts['low_tier']} were already scored Dial Third or lower, so they "
                     "won't be texted and weren't added")
    if counts.get("no_phone"):
        extra.append(f"{counts['no_phone']} have no phone even after a skip trace and need "
                     "deep prospecting")
    if counts.get("recently_queued"):
        extra.append(f"{counts['recently_queued']} are already waiting on an earlier Trestle list")
    if counts.get("read_error"):
        extra.append(f"{counts['read_error']} records couldn't be read and were left out")
    if counts.get("add_failed"):
        extra.append(f"{counts['add_failed']} couldn't be added to the list and will be "
                     "retried tomorrow")
    if extra:
        lines.append("_" + "; ".join(extra) + "._")
    return "\n".join(lines)


def run(post, client: DataSiftClient | None = None, today: date | None = None,
        commit: bool = True, force: bool = False) -> dict:
    """Queue today's Autotext leads that need scoring. `post(text) -> ts`.

    Once per day unless `force`. Posts nothing when no lead needs scoring."""
    today = today or _today()
    st = state.load_trestle_state()
    if not force and st.get("last_run") == today.isoformat():
        return {"skipped": "already ran today"}

    client = client or DataSiftClient.from_env()
    uuid = tag_uuid(client, AUTOTEXT_TAG)
    if not uuid:
        if commit:
            st["last_run"] = today.isoformat()
            state.save_trestle_state(st)
        post(f":warning: Couldn't find a DataSift tag named *{AUTOTEXT_TAG}*, so no Autotext "
             "leads were checked for phone scores today.")
        return {"error": f"tag {AUTOTEXT_TAG!r} not found"}

    records = [r.get("uuid") or r.get("saved_uuid")
               for r in client.search_properties({"any_tags": [uuid]})
               if r.get("uuid") or r.get("saved_uuid")]
    verdicts = _classify_many(client, records)
    counts: dict[str, int] = {}
    for v in verdicts.values():
        counts[v] = counts.get(v, 0) + 1

    queued = st.get("queued", {})
    cutoff = (today - timedelta(days=REQUEUE_DAYS)).isoformat()
    queued = {u: d for u, d in queued.items() if d > cutoff}  # forget old entries
    need = [u for u, v in verdicts.items() if v == "needs_scoring"]
    fresh = [u for u in need if u not in queued]
    counts["recently_queued"] = len(need) - len(fresh)
    title = list_title(today)
    result = {"autotext": len(records), "needs_scoring": len(need), "to_add": len(fresh),
              "list": title, **{k: v for k, v in counts.items() if k != "needs_scoring"}}

    if not commit:
        log.info("trestle queue (dry run): %s", result)
        return result

    st["last_run"] = today.isoformat()
    if not fresh:
        st["queued"] = queued
        state.save_trestle_state(st)
        log.info("trestle queue: nothing new to score (%s)", result)
        return result

    list_uuid = _ensure_list(client, title)
    if not list_uuid:
        state.save_trestle_state(st)
        post(f":warning: Couldn't create the DataSift list *{title}*; {len(fresh)} Autotext "
             "leads still need phone scores.")
        return {**result, "error": "list not created"}

    added, failed = _add_to_list(client, fresh, title)
    stamp = today.isoformat()
    queued.update({u: stamp for u in added})
    st["queued"] = queued
    state.save_trestle_state(st)
    if failed:
        counts["add_failed"] = failed
    if added:
        post(notice_text(title, len(added), counts))
    return {**result, "added": len(added), "failed": failed}
