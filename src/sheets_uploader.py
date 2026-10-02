"""Upload scrape run results to a monthly Google Spreadsheet.

Sheet structure:
  - One spreadsheet per month:  "SiftStack Leads - 2026-06"
  - One worksheet tab per run:  "Run 2026-06-26 09:30 (42 rec)"
  - Spreadsheet found by name in the Drive folder; created if missing.

Required scopes on the service account:
  - https://www.googleapis.com/auth/drive.file
  - https://www.googleapis.com/auth/spreadsheets
"""

import base64
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _build_services(service_account_key_b64: str | None = None):
    from googleapiclient.discovery import build

    if service_account_key_b64:
        from google.oauth2.service_account import Credentials
        key_json = json.loads(base64.b64decode(service_account_key_b64))
        creds = Credentials.from_service_account_info(key_json, scopes=SCOPES)
    else:
        import google.auth
        creds, _ = google.auth.default(scopes=SCOPES)

    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return drive, sheets


def _sheet_title(dt: datetime) -> str:
    return f"SiftStack Leads - {dt.strftime('%Y-%m')}"


def _find_sheet(drive_svc, folder_id: str, title: str) -> str | None:
    """Return spreadsheet ID if a matching file exists in the folder."""
    safe_title = title.replace("'", "\\'")
    q = (
        f"'{folder_id}' in parents "
        f"and name = '{safe_title}' "
        f"and mimeType = 'application/vnd.google-apps.spreadsheet' "
        f"and trashed = false"
    )
    resp = drive_svc.files().list(
        q=q, fields="files(id, name)", spaces="drive",
        includeItemsFromAllDrives=True, supportsAllDrives=True,
    ).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def _create_monthly_sheet(drive_svc, sheets_svc, folder_id: str, title: str) -> str:
    """Create a new blank spreadsheet in the folder and return its ID."""
    meta = {
        "name": title,
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [folder_id],
    }
    file = drive_svc.files().create(body=meta, fields="id", supportsAllDrives=True).execute()
    spreadsheet_id = file["id"]

    # Rename the default "Sheet1" tab to "Index"
    sheets_svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": 0, "title": "Index"},
                "fields": "title",
            }
        }]},
    ).execute()

    logger.info("Created monthly sheet '%s' (id=%s)", title, spreadsheet_id)
    return spreadsheet_id


def _build_row(notice) -> list:
    """Convert a NoticeData to a flat list matching SIFT_COLUMNS order."""
    from data_formatter import _split_name, _format_date_sift

    first, last = _split_name(notice.owner_name)

    def s(v) -> str:
        return str(v) if v not in (None, "") else ""

    return [
        s(notice.owner_name),
        s(notice.address),
        s(notice.city),
        s(notice.state),
        s(notice.zip),
        s(first),
        s(last),
        s(notice.owner_street),
        s(notice.owner_city),
        s(notice.owner_state),
        s(notice.owner_zip),
        s(_format_date_sift(notice.date_added)),
        s(notice.notice_type),
        s(notice.county),
        s(notice.decedent_name),
        s(_format_date_sift(notice.auction_date)),
        s(notice.zip_plus4),
        s(notice.latitude),
        s(notice.longitude),
        s(notice.dpv_match_code),
        s(notice.vacant),
        s(notice.rdi),
        s(notice.mls_status),
        s(notice.mls_listing_price),
        s(_format_date_sift(notice.mls_last_sold_date)),
        s(notice.mls_last_sold_price),
        s(notice.estimated_value),
        s(notice.estimated_equity),
        s(notice.equity_percent),
        s(notice.property_type),
        s(notice.bedrooms),
        s(notice.bathrooms),
        s(notice.sqft),
        s(notice.year_built),
        s(notice.lot_size),
        s(notice.parcel_id),
        s(notice.tax_delinquent_amount),
        s(notice.tax_delinquent_years),
        s(notice.deceased_indicator),
        s(notice.tax_owner_name),
        s(notice.owner_deceased),
        s(notice.date_of_death),
        s(notice.obituary_url),
        s(notice.decision_maker_name),
        s(notice.decision_maker_relationship),
        s(notice.decision_maker_status),
        s(notice.decision_maker_source),
        s(notice.decision_maker_street),
        s(notice.decision_maker_city),
        s(notice.decision_maker_state),
        s(notice.decision_maker_zip),
        s(notice.decision_maker_2_name),
        s(notice.decision_maker_2_relationship),
        s(notice.decision_maker_2_status),
        s(notice.decision_maker_3_name),
        s(notice.decision_maker_3_relationship),
        s(notice.decision_maker_3_status),
        s(notice.obituary_source_type),
        s(notice.heir_search_depth),
        s(notice.heirs_verified_living),
        s(notice.heirs_verified_deceased),
        s(notice.heirs_unverified),
        s(notice.dm_confidence),
        s(notice.dm_confidence_reason),
        s(notice.missing_data_flags),
        s(notice.heir_map_json),
        s(notice.mailable),
        s(notice.entity_type),
        s(notice.entity_person_name),
        s(notice.entity_person_role),
        s(notice.entity_research_source),
        s(notice.entity_research_confidence),
        s(notice.source_url),
        s(notice.run_id),
    ]


def _add_run_tab(sheets_svc, spreadsheet_id: str, notices: list, tab_name: str) -> str:
    """Add a worksheet, write header + data rows, return the direct tab URL."""
    from data_formatter import SIFT_COLUMNS

    # Create the new worksheet
    result = sheets_svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
    ).execute()
    sheet_id = result["replies"][0]["addSheet"]["properties"]["sheetId"]

    # Build payload: header row + data rows
    rows = [list(SIFT_COLUMNS)]
    for n in notices:
        rows.append(_build_row(n))

    sheets_svc.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()

    # Freeze + bold the header row
    sheets_svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            },
        ]},
    ).execute()

    tab_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid={sheet_id}"
    logger.info("Tab '%s': %d records → %s", tab_name, len(notices), tab_url)
    return tab_url


def upload_to_sheets(
    notices: list,
    folder_id: str,
    service_account_key_b64: str | None = None,
    run_dt: datetime | None = None,
) -> dict:
    """Upload notices to the monthly Google Sheet.

    Creates the spreadsheet if it doesn't exist for this month.
    Adds a new tab named "Run YYYY-MM-DD HH:MM (N rec)".

    Returns:
        {
            "success": True/False,
            "sheet_url": "https://docs.google.com/...",
            "tab_name": "Run 2026-06-26 09:30 (42 rec)",
            "spreadsheet_id": "...",
            "records": N,
        }
    """
    try:
        drive_svc, sheets_svc = _build_services(service_account_key_b64)

        now = run_dt or datetime.now()
        title = _sheet_title(now)

        spreadsheet_id = _find_sheet(drive_svc, folder_id, title)
        if spreadsheet_id:
            logger.info("Found existing sheet '%s' (id=%s)", title, spreadsheet_id)
        else:
            spreadsheet_id = _create_monthly_sheet(drive_svc, sheets_svc, folder_id, title)

        count = len(notices)
        # Google Sheets tab name limit is 100 chars; this stays well under that
        tab_name = f"Run {now.strftime('%Y-%m-%d %H:%M')} ({count} rec)"

        sheet_url = _add_run_tab(sheets_svc, spreadsheet_id, notices, tab_name)

        return {
            "success": True,
            "sheet_url": sheet_url,
            "tab_name": tab_name,
            "spreadsheet_id": spreadsheet_id,
            "records": count,
        }

    except Exception as e:
        logger.exception("Google Sheets upload failed")
        return {"success": False, "error": str(e)}
