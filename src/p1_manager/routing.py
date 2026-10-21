"""Put new P1 records straight onto the county's Raw board phase.

Dan, 2026-10-21: every new record the weekly job finds goes onto SiftLine
board "Raw", phase = the record's county ("Raw / Canyon", "Raw / Ada"),
without waiting for someone to reply in Slack. The Slack post says where they
went; replying in its thread with another board + phase still adds them
there too (slack_app.handle_reply).

The county comes from the preset's own address block, the same structured
field monthly.county_scope uses, not from the preset name (names use several
separator styles). Board and phase are matched by name, ignoring case, dash
style and a trailing " County", and nothing is created unless both exist. A
record that already has a card anywhere on that board is skipped, so a re-run
never doubles a card and a lead already moved along stays where it is.
"""
from __future__ import annotations

import logging
import os
import re

from datasift_client import ApiError, DataSiftClient

log = logging.getLogger("p1_routing")

AUTO_BOARD = os.getenv("P1_AUTO_BOARD", "Raw")


def replace_owners() -> bool:
    """P1 pulls swap in DataSift's current owner (Dan, 2026-10-21), unlike the
    upstream doctrine of never overwriting owners. P1_REPLACE_OWNERS=0 = off."""
    return os.getenv("P1_REPLACE_OWNERS", "1").strip().lower() not in ("0", "off", "false", "no")


def ensure_replace_owners(client: DataSiftClient, details: dict) -> list[str]:
    """Turn "swap owners" on for every P1 preset that has it off. `details` is
    preset name -> preset detail (as weekly.run already fetched). Returns the
    names changed. Only that one field is sent, never filter_data."""
    changed = []
    for name, d in details.items():
        if d.get("id") is None or d.get("replace_owners_enabled"):
            continue
        try:
            client.update_preset(d["id"], replace_owners_enabled=True)
            d["replace_owners_enabled"] = True
            changed.append(name)
        except ApiError as e:
            log.warning("could not turn on swap owners for %s: %s", name, str(e)[:120])
    return changed


def enabled() -> bool:
    return os.getenv("P1_AUTO_ROUTE", "1").strip().lower() not in ("0", "off", "false", "no")


def norm(s: str) -> str:
    """Lowercase, every dash/slash/pipe/colon as a space, single spaces, padded.
    Slack turns a typed hyphen into an en dash, DataSift strips em dashes, and
    'Raw - Canyon' vs 'Raw – Canyon' must not decide whether a card moves."""
    return " " + " ".join(re.sub(r"[\-–—_/|:,]+", " ", (s or "").lower()).split()) + " "


def titled(items) -> list[dict]:
    """Boards and columns carry their name as `title` or `name` depending on
    the endpoint (account_blueprint/export.py reads both); match on either."""
    return [{**x, "title": x.get("title") or x.get("name") or ""}
            for x in (items or []) if isinstance(x, dict)]


def county_of(preset_detail: dict) -> str:
    """'Canyon' from the preset's address block ('Canyon County' -> 'Canyon')."""
    addr = ((preset_detail.get("filter_data") or {}).get("addresses") or [{}])[0]
    county = (addr.get("county") or "").strip()
    return re.sub(r"\s+county$", "", county, flags=re.I).strip()


def target(client: DataSiftClient, county: str) -> tuple[dict | None, dict | None, str]:
    """(board, column, "") for AUTO_BOARD / <county>, else (None, None, why)."""
    if not county:
        return None, None, "couldn't tell which county this preset covers"
    board = next((b for b in titled(client.boards()) if norm(b["title"]) == norm(AUTO_BOARD)), None)
    if not board:
        return None, None, f"no SiftLine board named {AUTO_BOARD}"
    want = norm(county)
    cols = titled(client.board_columns(board["uuid"]))
    col = next((c for c in cols if norm(re.sub(r"\s+county$", "", c["title"], flags=re.I)) == want),
               None)
    if not col:
        return board, None, f"no phase named {county} on the {board['title']} board"
    return board, col, ""


def _already_on_board(client: DataSiftClient, property_uuid: str, board_uuid: str) -> bool:
    """Any card on this board, in any phase. A lead someone already moved
    along (Canyon -> Follow Up) must not get a second card back in Canyon.
    Card shape as obituary_crm_push.py reads it: card["board"]["uuid"]."""
    try:
        cards = client.property_cards(property_uuid)
    except ApiError:
        return False
    return any(((c.get("board") or {}).get("uuid") if isinstance(c.get("board"), dict)
                else c.get("board")) == board_uuid for c in cards or [])


def _uuid_of(x) -> str:
    return (x or {}).get("uuid", "") if isinstance(x, dict) else (x or "")


def remove_cards(client: DataSiftClient, property_uuids: list[str], board_uuid: str,
                 column_uuid: str) -> tuple[int, int]:
    """Delete each record's card in exactly this board phase (the one route()
    made). Returns (removed, kept): kept = records whose card has since been
    moved elsewhere on the board, which are left alone."""
    removed = kept = 0
    for uuid in property_uuids:
        try:
            cards = client.property_cards(uuid)
        except ApiError as e:
            log.warning("could not read cards for %s: %s", uuid, str(e)[:120])
            continue
        on_board = [c for c in cards or [] if _uuid_of(c.get("board")) == board_uuid]
        mine = [c for c in on_board if _uuid_of(c.get("column")) == column_uuid]
        for card in mine:
            try:
                client.delete_siftline_card(column_uuid, card["uuid"])
                removed += 1
            except ApiError as e:
                log.warning("could not remove card %s: %s", card.get("uuid"), str(e)[:120])
        if on_board and not mine:
            kept += 1
    return removed, kept


def route(client: DataSiftClient, preset_detail: dict, property_uuids: list[str]) -> dict:
    """Create cards on Raw / <county>. Returns what happened, for the Slack post:
    {board, phase, board_uuid, column_uuid, created, skipped, failed} or {error}."""
    county = county_of(preset_detail)
    board, col, why = target(client, county)
    if not col:
        return {"error": why, "county": county}
    created = skipped = failed = 0
    for uuid in property_uuids:
        if _already_on_board(client, uuid, board["uuid"]):
            skipped += 1
            continue
        try:
            client.create_siftline_card(col["uuid"], uuid)
            created += 1
        except ApiError as e:
            failed += 1
            log.warning("card create failed for %s on %s / %s: %s",
                        uuid, board["title"], col["title"], str(e)[:120])
    return {"board": board["title"], "phase": col["title"], "board_uuid": board["uuid"],
            "column_uuid": col["uuid"], "created": created, "skipped": skipped, "failed": failed}
