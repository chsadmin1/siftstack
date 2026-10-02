"""Saturday job: find records newly added to P1 presets this past week, tag
them, and ask (via Slack) whether/where they go on a SiftLine board+phase.

THE KEY DISCOVERY (verified live 2026-09-22) that shapes this whole module:
map.reisift.io's own POST /properties/search/ result rows already carry
`saved` (bool, already in this account), `saved_date` (when it was added --
exactly the field this job needs), `saved_uuid` (the apiv2 property uuid) and
`saved_tags`. Re-running each P1 preset's own filter_data through that
endpoint and filtering client-side on saved_date is simpler and more direct
than any tag/list-registry lookup: it needs no assumption about whether a
preset's own auto-add `tags` value has registered as a real Tag object (it
often hasn't -- see datasift_client.py's docstring), it's exactly what the
preset itself uses to decide membership, and saved_uuid/saved_tags are handed
back for free so tagging and dedup need no extra round trip.

This is ADDITIVE to src/weekly_new_records.py, which stays untouched --
that job answers "how many new records landed account-wide"; this one answers
"which P1 preset did each new record come from, and does it need a push to
SiftLine."
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import state
import tags as tagmod
from datasift_client import DataSiftClient

log = logging.getLogger("p1_weekly")

WORLD = [{"lon": -17.89461189115002, "lat": 72.08452694723852},
         {"lon": -17.89461189115002, "lat": -13.881763595427103},
         {"lon": -163.30518783395442, "lat": -13.881763595427103},
         {"lon": -163.30518783395442, "lat": 72.08452694723852}]
DEFAULT_WINDOW_DAYS = 7


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def p1_presets(client: DataSiftClient) -> list[dict]:
    return [p for p in client.list_presets()
            if (p.get("name") or "").startswith("P1 -") and p.get("is_active", True)]


def search_preset(client: DataSiftClient, preset_detail: dict) -> list[dict]:
    fd = preset_detail["filter_data"]
    addr_rich = fd["addresses"][0]
    addr_thin = dict(addr_rich, search=addr_rich["county"], type="county")
    r = client.map_call("/properties/search/", "POST", {
        "result_index": 1, "with_boundaries": False,
        "filters": fd["filters"], "addresses": [addr_thin], "polygon": WORLD})
    return r.get("results") or r.get("data") or []


def find_new_for_preset(client: DataSiftClient, preset_summary: dict, since: datetime) -> list[dict]:
    detail = client.get_preset(preset_summary["id"])
    rows = search_preset(client, detail)
    out = []
    for row in rows:
        if not row.get("saved") or not row.get("saved_date"):
            continue
        try:
            saved_dt = datetime.fromisoformat(row["saved_date"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if saved_dt >= since:
            out.append(row)
    return out


def run(*, commit: bool = True, window_days: int = DEFAULT_WINDOW_DAYS,
        post_slack=None, only_presets: list[str] | None = None,
        update_cursor: bool = True) -> dict:
    """post_slack(preset_name, new_rows) -> thread_ts, or None to skip posting
    (e.g. in --dry-run). Kept as an injectable callback so this module has no
    hard Slack import until slack_app.py exists.

    only_presets: exact preset names to limit this run to (e.g. for a small
    live test rather than the full account sweep). update_cursor: False keeps
    a scoped test run from advancing the shared "since" cursor that the real
    scheduled run relies on."""
    client = DataSiftClient.from_env()
    weekly_state = state.load_weekly_state()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)
    if weekly_state.get("last_run_iso"):
        try:
            since = datetime.fromisoformat(weekly_state["last_run_iso"].replace("Z", "+00:00"))
        except ValueError:
            pass

    presets = p1_presets(client)
    if only_presets:
        presets = [p for p in presets if p["name"] in only_presets]
    log.info("Scanning %d P1 presets for records saved since %s", len(presets), _iso(since))

    per_preset: dict[str, list[dict]] = {}
    for p in presets:
        try:
            new_rows = find_new_for_preset(client, p, since)
        except Exception as exc:  # noqa: BLE001 -- one bad preset should not kill the run
            log.warning("  %s: skipped (%s)", p["name"], exc)
            continue
        if new_rows:
            per_preset[p["name"]] = new_rows
            log.info("  %s: %d new", p["name"], len(new_rows))

    results = {}
    for preset_name, rows in per_preset.items():
        uuids = [r["saved_uuid"] for r in rows]
        missing_p1 = [r["saved_uuid"] for r in rows if "P1" not in (r.get("saved_tags") or [])]
        if commit:
            if missing_p1:
                tagmod.backfill_missing_p1(client, missing_p1)
            thread_ts = post_slack(preset_name, rows) if post_slack else None
            if thread_ts:
                state.save_pending_ask(thread_ts, {
                    "preset_name": preset_name,
                    "property_uuids": uuids,
                    "count": len(uuids),
                    "since": _iso(since),
                })
        results[preset_name] = {"new_count": len(rows), "backfilled_p1": len(missing_p1)}

    if commit and update_cursor:
        weekly_state["last_run_iso"] = _iso(now)
        state.save_weekly_state(weekly_state)

    return {"since": _iso(since), "presets_with_new_records": len(per_preset),
            "detail": results}


if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                     help="scan and report only -- no tag writes, no Slack post, no state update")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    args = ap.parse_args()
    res = run(commit=not args.dry_run, window_days=args.window_days)
    print(json.dumps(res, indent=2))
