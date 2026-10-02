"""28th-of-the-month job: re-check the playbook, create presets for newly
appearing P1 combos, fully re-pull every P1 preset's records, tag everything,
export a CSV, and build a month-over-month change report.

County/state scope is auto-discovered from existing "P1 -" presets' own
filter_data.addresses (not parsed from the preset NAME -- names use at least
three different separator styles in production, "(County, ST)" / "—
County, ST" / "- County, ST", so the address block is the only reliable
source). New presets this job creates use the dominant existing convention,
"P1 - <label> (<County>, <ST>)" -- confirm with Dan before the first real run
if a different style is wanted; see default_preset_name().

Every individual playbook combo stays its own preset/pull, never merged with
another even when two combos look logically redundant (Dan, 2026-09-22 call:
preserve them exactly as supplied and let de-dup remove duplicate records
afterward, don't consolidate on Boolean/filter logic).
"""
from __future__ import annotations

import csv
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import playbook as pb
import state
import tags as tagmod
from datasift_client import DataSiftClient
from weekly import WORLD, p1_presets, search_preset  # reuse, don't duplicate

log = logging.getLogger("p1_monthly")

DRIFT_THRESHOLD = 0.15  # 15% swing in list_size/marg_dpd -> worth flagging as "changed"


def county_scope(presets: list[dict], client: DataSiftClient) -> tuple[list[dict], dict]:
    """([{county, state, county_key}], {county_key: [preset names]}) from each
    preset's own address block (not its name -- names use at least 3 different
    separator conventions in this account, addresses are structured)."""
    seen: dict[str, dict] = {}
    by_county: dict[str, list[str]] = {}
    for p in presets:
        detail = client.get_preset(p["id"])
        addr = (detail.get("filter_data") or {}).get("addresses") or [{}]
        county, st = addr[0].get("county"), addr[0].get("state")
        if not county or not st:
            continue
        key = f"{st}-{county}"
        seen.setdefault(key, {"county": county, "state": st, "county_key": key})
        by_county.setdefault(key, []).append(p["name"])
    return list(seen.values()), by_county


def _name_suffix(existing_preset_name: str) -> str:
    """The "(County, ST)" / "— County, ST" / "- County, ST" tail of an
    existing preset name, found by stripping its own label back off."""
    label = label_from_preset_name(existing_preset_name)
    prefix = f"P1 - {label}"
    if existing_preset_name.startswith(prefix):
        return existing_preset_name[len(prefix):]
    return ""


def default_preset_name(label: str, county: str, state_abbr: str,
                         *, sibling_names: list[str] | None = None) -> str:
    """Match whatever naming convention this county's OTHER P1 presets
    already use (there are 3 different styles across the account, per-county
    consistent) rather than a single hardcoded global format -- a mismatched
    name here means the dedupe check in create_presets_for_new_combos can't
    find the sibling and creates a duplicate preset. Falls back to the
    "(County, ST)" style only for a county with no existing P1 presets yet."""
    if sibling_names:
        suffix = _name_suffix(sibling_names[0])
        if suffix:
            return f"P1 - {label}{suffix}"
    return f"P1 - {label} ({county}, {state_abbr})"


def label_from_preset_name(name: str) -> str:
    """Best-effort combo label out of an existing preset's own name, for the
    tags.py composite tag. Strips the leading 'P1 - ' and any trailing
    county/state parenthetical or dash suffix."""
    n = name
    if n.startswith("P1 - "):
        n = n[5:]
    for sep in (" (", " — ", " - "):
        idx = n.find(sep)
        if idx != -1:
            n = n[:idx]
            break
    return n.strip()


def diff_county(current_rows: list[dict], previous_rows: list[dict] | None) -> dict:
    current = {r["label"]: r for r in current_rows}
    previous = {r["label"]: r for r in (previous_rows or [])}
    added = sorted(set(current) - set(previous))
    dropped = sorted(set(previous) - set(current))
    changed = []
    for label in set(current) & set(previous):
        c, p = current[label], previous[label]
        for field in ("list_size", "marg_dpd"):
            cv, pv = c.get(field) or 0, p.get(field) or 0
            if pv and abs(cv - pv) / pv >= DRIFT_THRESHOLD:
                changed.append({"label": label, "field": field, "was": pv, "now": cv})
                break
    return {"added": added, "dropped": dropped, "changed": changed}


def create_presets_for_new_combos(client: DataSiftClient, county: str, state_abbr: str,
                                   fips: str, added_labels: list[dict], *,
                                   sibling_names: list[str], commit: bool) -> list[dict]:
    existing_names = {p["name"] for p in client.list_presets()}
    report = []
    for label in added_labels:
        name = default_preset_name(label, county, state_abbr, sibling_names=sibling_names)
        if name in existing_names:
            report.append({"label": label, "name": name, "status": "already_exists"})
            continue
        try:
            filters = pb.resolve_combo_filters(label)
        except pb.UnmappedComboError as exc:
            report.append({"label": label, "name": name, "status": "unmapped", "detail": str(exc)})
            continue
        if not commit:
            report.append({"label": label, "name": name, "status": "would_create", "filters": filters})
            continue
        address = pb.county_address(fips, county, state_abbr, rich=True)
        created = client.create_preset(name, filters=filters, address=address,
                                        tags=["P1", name])
        readback = client.get_preset(created["id"])
        ok = readback.get("filter_data", {}).get("filters") == filters
        report.append({"label": label, "name": name,
                       "status": "created" if ok else "created_readback_mismatch",
                       "id": created.get("id")})
    return report


def full_repull_and_tag(client: DataSiftClient, presets: list[dict], *,
                         commit: bool, pull_date: date, csv_path: Path) -> dict:
    """Re-enumerate every P1 preset's current matches, add any not yet in the
    account (auto_add's own safety net -- these presets are already Dan-
    approved, this just catches lag), tag everything per tags.py, write the
    full CSV."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows_out = []
    per_preset_counts = {}

    for p in presets:
        detail = client.get_preset(p["id"])
        label = label_from_preset_name(p["name"])
        try:
            rows = search_preset(client, detail)
        except Exception as exc:  # noqa: BLE001
            log.warning("  %s: search failed (%s)", p["name"], exc)
            continue

        saved_rows = [r for r in rows if r.get("saved") and r.get("saved_uuid")]
        gap_rows = [r for r in rows if not r.get("saved")]

        added_count = len(gap_rows)  # reported either way; only ACTED on if commit
        if commit and gap_rows:
            addr = detail["filter_data"]["addresses"][0]
            addr_thin = dict(addr, search=addr["county"], type="county")
            client.map_call("/properties/add-properties-by-query/", "POST", {
                "auto_add_enabled": False, "lists": [], "tags": ["P1", p["name"]],
                "replace_owners": False,
                "query": {"result_index": 1, "with_boundaries": False,
                          "filters": detail["filter_data"]["filters"],
                          "addresses": [addr_thin], "polygon": WORLD}})

        uuids = [r["saved_uuid"] for r in saved_rows]
        if commit and uuids:
            tagmod.tag_pull(client, uuids, label, pull_date=pull_date)

        per_preset_counts[p["name"]] = {
            "already_saved": len(saved_rows),
            "not_yet_saved": added_count,
            "gap_fill_action": "added" if commit and gap_rows else ("would_add" if gap_rows else "n/a"),
        }
        for r in saved_rows:
            rows_out.append({
                "preset": p["name"], "list_name": label,
                "address": r.get("address"), "county": r.get("county"), "state": r.get("state"),
                "estimated_value": r.get("estimatedValue"), "equity_percent": r.get("equityPercent"),
                "saved_uuid": r.get("saved_uuid"), "saved_date": r.get("saved_date"),
            })

    if rows_out:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            w.writeheader()
            w.writerows(rows_out)

    return {"total_records": len(rows_out), "per_preset": per_preset_counts,
            "csv_path": str(csv_path)}


def build_report_text(result: dict) -> str:
    """The human-readable change report Dan asked for: combos added/dropped/
    changed this month, and the running total of P1 presets."""
    lines = [f":bar_chart: *P1 Monthly Refresh -- {result['month']}*", ""]
    any_changes = False
    for c in result["counties"]:
        d = c["diff"]
        created = [r for r in c["new_presets"] if r["status"] == "created"]
        unmapped = [r for r in c["new_presets"] if r["status"] == "unmapped"]
        if not (created or d["dropped"] or d["changed"] or unmapped):
            continue
        any_changes = True
        lines.append(f"*{c['county_key']}*")
        for r in created:
            lines.append(f"  ➕ new preset: {r['label']}")
        for r in unmapped:
            lines.append(f"  ❓ skipped (unmapped field): {r['label']}")
        for label in d["dropped"]:
            lines.append(f"  ➖ dropped from playbook: {label}")
        for ch in d["changed"]:
            lines.append(f"  \U0001f504 {ch['label']}: {ch['field']} {ch['was']} -> {ch['now']}")
        lines.append("")
    if not any_changes:
        lines.append("No P1 combination changes this month.")
        lines.append("")
    lines.append(f"*Total P1 presets: {result['total_p1_presets']}*")
    lines.append(f"Records re-pulled and tagged: {result['repull']['total_records']}")
    return "\n".join(lines)


def run(*, commit: bool = True, pull_date: date | None = None) -> dict:
    client = DataSiftClient.from_env()
    today = pull_date or date.today()
    month_key = today.strftime("%Y-%m")

    presets = p1_presets(client)
    counties, presets_by_county = county_scope(presets, client)
    log.info("County scope (from existing P1 presets): %s",
             [c["county_key"] for c in counties])

    county_reports = []
    for c in counties:
        entry = pb.resolve_county(c["state"], c["county"])
        current_rows = pb.p1_rows(entry)
        prev = state.latest_monthly_snapshot(c["county_key"])
        diff = diff_county(current_rows, prev.get("rows") if prev else None)

        create_report = []
        if diff["added"]:
            fips = entry["fips"]
            create_report = create_presets_for_new_combos(
                client, c["county"], c["state"], fips, diff["added"],
                sibling_names=presets_by_county.get(c["county_key"], []), commit=commit)

        created_now = sum(1 for r in create_report if r["status"] == "created")
        if commit:
            state.save_monthly_snapshot(c["county_key"], {
                "month": month_key, "rows": current_rows,
                "total_presets": len(presets_by_county.get(c["county_key"], [])) + created_now,
            })

        county_reports.append({"county_key": c["county_key"], "diff": diff,
                               "new_presets": create_report})

    # Full re-pull happens across the CURRENT preset list (including anything
    # just created above), so a brand-new combo gets exported this same run.
    all_presets = p1_presets(client) if any(cr["new_presets"] for cr in county_reports) else presets
    csv_path = state.OUTPUT_DIR / f"p1_pull_{today.isoformat()}.csv"
    repull = full_repull_and_tag(client, all_presets, commit=commit,
                                  pull_date=today, csv_path=csv_path)

    result = {
        "month": month_key,
        "counties": county_reports,
        "repull": repull,
        "total_p1_presets": len(all_presets),
        "preset_count_history": state.preset_count_history() if commit else None,
    }
    result["report_text"] = build_report_text(result)
    return result


if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                     help="diff + plan only -- no preset creation, no tag writes, "
                          "no record adds, no state update, no CSV")
    args = ap.parse_args()
    res = run(commit=not args.dry_run)
    print(json.dumps(res, indent=2, default=str))
