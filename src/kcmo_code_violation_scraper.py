"""Scraper for KCMO code violations via Kansas City Open Data Socrata API.

Three live datasets (all updated regularly as of June 2026):
  tezm-fh2e — Open Property Violations (311 complaints, updated daily)
  ax3m-jhxx  — Dangerous Buildings List (~375 properties, updated monthly)
  w5nm-8qv8  — Open Exterior Building Violations (formal citations still open)

Covers: Jackson County, Missouri (Kansas City city limits only, ~75% of county).
Cities outside KC limits (Independence, Blue Springs, Lee's Summit, Grandview,
Raytown) require courthouse photo import — no public API available.

No login required. No CAPTCHA. No Playwright. Pure HTTP GET + Socrata SoQL.
"""

import logging
import re
import time
from datetime import datetime, timedelta

import requests

import config
from config import STATE_FILE
from notice_parser import NoticeData

logger = logging.getLogger(__name__)

_BASE = "https://data.kcmo.org/resource"
_OPEN_PROP_ID = "tezm-fh2e"    # 311 complaints, updated daily
_DANGER_BLDG_ID = "ax3m-jhxx"  # Dangerous buildings, updated monthly
_EXTERIOR_VIO_ID = "w5nm-8qv8" # Open formal citations, exterior building

_PAGE_SIZE = 1000

# Sub-types that signal vacant/abandoned properties — tagged HIGH PRIORITY in raw_text
_HIGH_PRIORITY_SUBTYPES = {
    "Open to Entry",
    "Failure to Register Vacant Property",
    "NHS Preservation",
    "Contractor Board Up Referrals",
    "Contractor Abatement Referral",
}


def _parse_incident_address(full_addr: str) -> tuple[str, str]:
    """Parse '123 Main St Kansas City 64110' → (street, zip)."""
    full_addr = full_addr.strip()
    m = re.match(r"^(.*?)\s+Kansas City\s+(\d{5})\s*$", full_addr, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2)
    # Fallback: zip at end, strip city name
    m2 = re.search(r"(\d{5})\s*$", full_addr)
    if m2:
        zip_code = m2.group(1)
        street = re.sub(r"\s*Kansas City\s*$", "", full_addr[: m2.start()], flags=re.IGNORECASE).strip()
        return street, zip_code
    return full_addr, ""


def _fetch_open_property_violations(
    since_date: str, fallback_date: str, max_violations: int
) -> list[NoticeData]:
    """Pull tezm-fh2e — 311 property complaints, updated daily."""
    url = f"{_BASE}/{_OPEN_PROP_ID}.json"
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    notices: list[NoticeData] = []
    seen_ids: set[str] = set()
    offset = 0

    while True:
        params = {
            "$where": f"open_date_time >= '{since_date}T00:00:00.000'",
            "$select": (
                "reported_issue,workorder_,open_date_time,current_status,"
                "issue_sub_type,incident_address,last_updated"
            ),
            "$limit": _PAGE_SIZE,
            "$offset": offset,
            "$order": "open_date_time ASC",
        }
        try:
            resp = session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            rows = resp.json()
        except Exception as e:
            logger.error("KCMO open-property-violations failed (offset=%d): %s", offset, e)
            break

        if not rows:
            break

        logger.debug("  open-violations: %d rows at offset %d", len(rows), offset)

        for row in rows:
            vid = str(row.get("reported_issue") or row.get("workorder_") or "")
            if vid and vid in seen_ids:
                continue
            if vid:
                seen_ids.add(vid)

            raw_addr = (row.get("incident_address") or "").strip()
            street, zip_code = _parse_incident_address(raw_addr)
            if not street:
                continue

            sub_type = (row.get("issue_sub_type") or "").strip()
            priority = "HIGH PRIORITY | " if sub_type in _HIGH_PRIORITY_SUBTYPES else ""
            raw_text = f"{priority}Property Violation: {sub_type}" if sub_type else "Property Violation"

            date_str = (row.get("open_date_time") or "").split("T")[0] or fallback_date
            case_num = row.get("workorder_") or vid
            source_url = (
                f"https://data.kcmo.org/d/{_OPEN_PROP_ID}?workorder_={case_num}"
                if case_num else f"https://data.kcmo.org/d/{_OPEN_PROP_ID}"
            )

            notices.append(NoticeData(
                notice_type="code_violation",
                county="Jackson",
                state="MO",
                city="Kansas City",
                address=street,
                zip=zip_code,
                date_added=date_str,
                source_url=source_url,
                raw_text=raw_text,
            ))

            if max_violations and len(notices) >= max_violations:
                return notices

        if len(rows) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
        time.sleep(0.5)

    logger.info("KCMO open-property-violations: %d since %s", len(notices), since_date)
    return notices


def _fetch_dangerous_buildings(
    since_date: str, mode: str, fallback_date: str
) -> list[NoticeData]:
    """Pull ax3m-jhxx — dangerous buildings list.

    Historical mode pulls ALL active cases (only ~375 total, worth having all).
    Daily mode pulls only cases opened since last run.
    """
    url = f"{_BASE}/{_DANGER_BLDG_ID}.json"
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    notices: list[NoticeData] = []
    seen_ids: set[str] = set()
    offset = 0

    params_base: dict = {
        "$select": "casenumber,address,zip_code,case_opened,statusofcase,pin,neighborhood",
        "$limit": _PAGE_SIZE,
        "$order": "case_opened DESC",
    }
    if mode == "daily":
        params_base["$where"] = f"case_opened >= '{since_date}T00:00:00.000'"

    while True:
        params = {**params_base, "$offset": offset}
        try:
            resp = session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            rows = resp.json()
        except Exception as e:
            logger.error("KCMO dangerous-buildings failed (offset=%d): %s", offset, e)
            break

        if not rows:
            break

        logger.debug("  dangerous-buildings: %d rows at offset %d", len(rows), offset)

        for row in rows:
            case_num = str(row.get("casenumber") or "")
            if case_num and case_num in seen_ids:
                continue
            if case_num:
                seen_ids.add(case_num)

            street = (row.get("address") or "").strip()
            if not street:
                continue

            zip_code = str(row.get("zip_code") or "").strip()
            status = (row.get("statusofcase") or "Ongoing Case").strip()
            neighborhood = (row.get("neighborhood") or "").strip()

            if "Demolition" in status or "Asbestos" in status:
                priority = "HIGH PRIORITY | "
            elif "Pre-Bid" in status:
                priority = "URGENT | "
            else:
                priority = ""

            parts = [f"{priority}Dangerous Building | Status: {status}"]
            if neighborhood:
                parts.append(f"Neighborhood: {neighborhood}")
            raw_text = " | ".join(parts)

            date_str = (row.get("case_opened") or "").split("T")[0] or fallback_date
            source_url = (
                f"https://data.kcmo.org/d/{_DANGER_BLDG_ID}?casenumber={case_num}"
                if case_num else f"https://data.kcmo.org/d/{_DANGER_BLDG_ID}"
            )

            notice = NoticeData(
                notice_type="code_violation",
                county="Jackson",
                state="MO",
                city="Kansas City",
                address=street,
                zip=zip_code,
                date_added=date_str,
                source_url=source_url,
                raw_text=raw_text,
            )
            if row.get("pin"):
                try:
                    notice.parcel_id = str(int(float(row["pin"])))
                except (ValueError, TypeError):
                    notice.parcel_id = str(row["pin"])

            notices.append(notice)

        if len(rows) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
        time.sleep(0.5)

    logger.info("KCMO dangerous-buildings: %d (mode=%s)", len(notices), mode)
    return notices


def _fetch_exterior_violations(fallback_date: str, max_violations: int) -> list[NoticeData]:
    """Pull w5nm-8qv8 — open formal citations for exterior building condition.

    This is a pre-filtered view of EnerGov showing only currently open exterior
    violations. No date filter applied — whatever is there is currently active.
    """
    url = f"{_BASE}/{_EXTERIOR_VIO_ID}.json"
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    notices: list[NoticeData] = []
    seen_ids: set[str] = set()
    offset = 0

    while True:
        params = {
            "$select": (
                "violationid,casenumber,street_address,postalcode,"
                "description,ord_text,vio_status,date_to_comply,pin"
            ),
            "$limit": _PAGE_SIZE,
            "$offset": offset,
        }
        try:
            resp = session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            rows = resp.json()
        except Exception as e:
            logger.error("KCMO exterior-violations failed (offset=%d): %s", offset, e)
            break

        if not rows:
            break

        logger.debug("  exterior-violations: %d rows at offset %d", len(rows), offset)

        for row in rows:
            vid = str(row.get("violationid") or "")
            if vid and vid in seen_ids:
                continue
            if vid:
                seen_ids.add(vid)

            street = (row.get("street_address") or "").strip()
            if not street:
                continue

            raw_zip = row.get("postalcode")
            zip_code = ""
            if raw_zip:
                try:
                    zip_code = str(int(float(raw_zip))).zfill(5)
                except (ValueError, TypeError):
                    zip_code = str(raw_zip)

            parts: list[str] = ["Open Exterior Building Violation"]
            if row.get("description"):
                parts.append(row["description"].strip())
            if row.get("ord_text"):
                parts.append(row["ord_text"].strip())
            if row.get("vio_status"):
                parts.append(f"Status: {row['vio_status'].strip()}")
            raw_text = " | ".join(parts)

            # Extract year from case number (NPD-YYYY-NNNNN); fall back to today
            case_num_str = row.get("casenumber") or ""
            year_match = re.search(r"NPD-(\d{4})-", case_num_str)
            date_str = f"{year_match.group(1)}-01-01" if year_match else fallback_date

            source_url = (
                f"https://data.kcmo.org/d/{_EXTERIOR_VIO_ID}?violationid={vid}"
                if vid else f"https://data.kcmo.org/d/{_EXTERIOR_VIO_ID}"
            )

            notice = NoticeData(
                notice_type="code_violation",
                county="Jackson",
                state="MO",
                city="Kansas City",
                address=street,
                zip=zip_code,
                date_added=date_str,
                source_url=source_url,
                raw_text=raw_text,
            )
            if row.get("pin"):
                try:
                    notice.parcel_id = str(int(float(row["pin"])))
                except (ValueError, TypeError):
                    notice.parcel_id = str(row["pin"])
            if row.get("date_to_comply"):
                try:
                    notice.auction_date = row["date_to_comply"].split("T")[0]
                except Exception:
                    pass

            notices.append(notice)

            if max_violations and len(notices) >= max_violations:
                return notices

        if len(rows) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
        time.sleep(0.5)

    logger.info("KCMO exterior-violations: %d open formal citations", len(notices))
    return notices


def fetch_kcmo_violations(
    counties: list[str],
    mode: str = "daily",
    since_date_override: str | None = None,
    max_violations: int = 0,
) -> list[NoticeData]:
    """Pull KCMO code violations from three live Socrata datasets.

    Datasets queried (all updated regularly as of June 2026):
      tezm-fh2e — Open Property Violations (311 complaints, updated daily)
      ax3m-jhxx  — Dangerous Buildings List (updated monthly)
      w5nm-8qv8  — Open Exterior Building Violations (formal open citations)

    Args:
        counties: County names to include. Only "Jackson" is currently supported.
        mode: "daily" or "historical" — controls default lookback window.
        since_date_override: ISO date (YYYY-MM-DD) to override mode-based lookback.
        max_violations: Stop after this many total records (0 = no limit).

    Returns:
        list[NoticeData] ready for the enrichment pipeline. owner_name is empty;
        DataSift skip trace finds contacts post-upload.
    """
    if not any(c.lower() == "jackson" for c in counties):
        logger.info("KCMO: no supported counties requested — skipping")
        return []

    if since_date_override:
        since_date = since_date_override
    elif mode == "daily":
        state_data = config.load_state(STATE_FILE)
        last = state_data.get("last_run_date")
        since_date = last or (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    else:
        since_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    fallback_date = datetime.now().strftime("%Y-%m-%d")
    logger.info("KCMO code violations: Jackson County, since %s (mode=%s)", since_date, mode)

    all_notices: list[NoticeData] = []

    # 1. Dangerous buildings — highest-urgency leads (facing demolition)
    db_notices = _fetch_dangerous_buildings(since_date, mode, fallback_date)
    all_notices.extend(db_notices)
    if max_violations and len(all_notices) >= max_violations:
        return all_notices[:max_violations]

    # 2. Open property violations (311 complaints) — date-filtered daily feed
    remaining = max_violations - len(all_notices) if max_violations else 0
    pv_notices = _fetch_open_property_violations(since_date, fallback_date, remaining)
    all_notices.extend(pv_notices)
    if max_violations and len(all_notices) >= max_violations:
        return all_notices[:max_violations]

    # 3. Open exterior building violations — formal citations still open in courts
    remaining = max_violations - len(all_notices) if max_violations else 0
    ev_notices = _fetch_exterior_violations(fallback_date, remaining)
    all_notices.extend(ev_notices)

    logger.info(
        "KCMO total: %d code violations (%d dangerous buildings, "
        "%d property violations, %d exterior citations)",
        len(all_notices), len(db_notices), len(pv_notices), len(ev_notices),
    )
    return all_notices[:max_violations] if max_violations else all_notices
