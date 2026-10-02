"""One DataSift/REISift client for every P1 List Manager module.

Auth: mints a user JWT from DATASIFT_EMAIL / DATASIFT_PASSWORD (same contract
as datasift_api_upload.Api and siftmap_pull.Api/Map -- see those files' own
docstrings for why: the Open API key 401s on newer /api/internal/ write
surfaces, so writes need the minted JWT; SiftMap (map.reisift.io) reads also
work fine on it). Re-mints every ~30 minutes or immediately on a 401, exactly
like the rest of this repo's write-heavy scripts.

Do NOT import src/reisift_session.py or anything under an external "_api"
checkout here -- that multi-account staff-impersonation system lives on a
different machine (verified unreachable from this fork) and every script that
depends on it is dead code on this account. Everything in this module works
off the flat .env vars alone, the same way weekly_new_records.py and
siftmap_pull.py already do.

THE SAFE BULK TAG SHAPE (read this before touching add_tags / remove_tags):
on 2026-08-11 a rollback meant to strip one record's tag instead stripped it
from the whole account (~900 records) because `properties` was passed at the
TOP LEVEL of the add-tags/remove-tags body, which that endpoint silently
ignores as a scope -- see src/restore_priority1.py's docstring, the incident
record. The only shape that actually scopes is `properties` NESTED INSIDE
query.must, with tags passed as TITLES, not uuids:

    {"query": {"must": {"properties": [...uuids...]}}, "tags": ["Some Tag"]}

bulk_add_tags() below builds exactly that shape and nothing else. Never call
/api/internal/property/add-tags/ or /remove-tags/ any other way.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

API_BASE = "https://apiv2.reisift.io"
MAP_BASE = "https://map.reisift.io"

TAG_CHUNK = 400          # matches priority_tags.py / restore_priority1.py
TAG_CHUNK_SLEEP = 1.0
SEARCH_PAGE = 250
MINT_REFRESH_SECS = 1800


def env() -> dict:
    """Env vars, with ./.env as a fallback. Mirrors datasift_api_upload.env():
    environment first so a Fly machine (no .env file, secrets only) still works.
    """
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


class ApiError(RuntimeError):
    def __init__(self, code: int, method: str, path: str, body: str):
        self.code, self.method, self.path, self.body = code, method, path, body
        super().__init__(f"HTTP {code} on {method} {path}: {body[:200]}")

    def json(self):
        try:
            return json.loads(self.body)
        except ValueError:
            return None


@dataclass
class DataSiftClient:
    """Auth + both API surfaces (apiv2 internal, map.reisift.io SiftMap)."""

    email: str
    password: str
    token: str = field(default="", init=False)
    minted: float = field(default=0.0, init=False)

    @classmethod
    def from_env(cls) -> "DataSiftClient":
        e = env()
        try:
            return cls(e["DATASIFT_EMAIL"], e["DATASIFT_PASSWORD"])
        except KeyError as exc:
            raise RuntimeError(
                f"{exc.args[0]} is not set. P1 List Manager mints its own JWT "
                "from DATASIFT_EMAIL / DATASIFT_PASSWORD (env var or .env)."
            ) from None

    def __post_init__(self):
        self._mint()

    def _mint(self):
        body = json.dumps({"email": self.email, "password": self.password}).encode()
        req = urllib.request.Request(
            API_BASE + "/api/token/", data=body, method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as r:
            self.token = json.loads(r.read())["access"]
        self.minted = time.time()

    # ---- apiv2.reisift.io (/api/internal/...) ----

    def api(self, path: str, method: str = "GET", body: dict | None = None,
            *, method_override: str | None = None, _retry: bool = True) -> dict:
        if time.time() - self.minted > MINT_REFRESH_SECS:
            self._mint()
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
        if method_override:
            headers["x-http-method-override"] = method_override
        req = urllib.request.Request(API_BASE + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                t = r.read().decode()
                return json.loads(t) if t.strip().startswith(("{", "[")) else {"raw": t}
        except urllib.error.HTTPError as e:
            if e.code == 401 and _retry:
                self._mint()
                return self.api(path, method, body, method_override=method_override, _retry=False)
            raise ApiError(e.code, method, path, e.read().decode())

    # ---- map.reisift.io (SiftMap presets) ----

    def map_call(self, path: str, method: str = "GET", body: dict | None = None,
                 *, timeout: int = 120) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(5):
            if time.time() - self.minted > MINT_REFRESH_SECS:
                self._mint()
            req = urllib.request.Request(
                MAP_BASE + path, data=data, method=method,
                headers={"Authorization": "Bearer " + self.token,
                         "Content-Type": "application/json", "accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    t = r.read().decode()
                    return json.loads(t) if t.strip().startswith(("{", "[")) else {"raw": t}
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt < 4:
                    self._mint()
                    continue
                if e.code in (429, 500, 502, 504) and attempt < 4:
                    time.sleep(4 * (attempt + 1))
                    continue
                raise ApiError(e.code, method, path, e.read().decode())
        raise RuntimeError(f"gave up on {method} {path}")

    # ---- SiftMap preset CRUD ----

    def list_presets(self) -> list[dict]:
        out: list[dict] = []
        path = "/filters/?scope=account&page_size=100"
        while path:
            r = self.map_call(path)
            rows = r.get("results") if isinstance(r, dict) else r
            out.extend(rows or [])
            nxt = r.get("next") if isinstance(r, dict) else None
            path = nxt.replace(MAP_BASE, "") if nxt else None
        return out

    def get_preset(self, preset_id: int) -> dict:
        return self.map_call(f"/filters/{preset_id}/")

    def create_preset(self, name: str, *, filters: dict, address: dict,
                       lists: list | None = None, tags: list | None = None,
                       auto_add_enabled: bool = True, description: str = "") -> dict:
        return self.map_call("/filters/", "POST", {
            "name": name, "description": description,
            "auto_add_enabled": auto_add_enabled, "replace_owners_enabled": False,
            "lists": lists or [], "tags": tags or [],
            "filter_data": {"filters": filters, "addresses": [address]},
        })

    def update_preset(self, preset_id: int, **fields) -> dict:
        """Partial update -- only send fields that are actually changing."""
        return self.map_call(f"/filters/{preset_id}/", "PATCH", fields)

    # ---- properties ----

    def search_properties(self, must: dict, *, page_size: int = SEARCH_PAGE) -> list[dict]:
        """All matching property rows (uuid + whatever the index returns), paginated."""
        out: list[dict] = []
        offset = 0
        while True:
            r = self.api("/api/internal/property/", "POST",
                          {"limit": page_size, "offset": offset, "query": {"must": must}},
                          method_override="GET")
            rows = (r.get("results") or r.get("data") or []) if isinstance(r, dict) else []
            out.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size
        return out

    def count_properties(self, must: dict) -> int:
        r = self.api("/api/internal/property/", "POST",
                      {"limit": 1, "offset": 0, "query": {"must": must}},
                      method_override="GET")
        return r.get("count", 0) if isinstance(r, dict) else 0

    # ---- the SAFE bulk tag shape ----

    def bulk_add_tags(self, property_uuids: list[str], tags: list[str],
                       *, extra_must: dict | None = None) -> int:
        """Add `tags` (titles) to exactly `property_uuids`. Never top-level `properties`
        -- see this module's docstring for the 2026-08-11 incident this avoids."""
        return self._bulk_tag_call("add-tags", property_uuids, tags, extra_must)

    def bulk_remove_tags(self, property_uuids: list[str], tags: list[str],
                          *, extra_must: dict | None = None) -> int:
        return self._bulk_tag_call("remove-tags", property_uuids, tags, extra_must)

    def _bulk_tag_call(self, endpoint: str, property_uuids: list[str], tags: list[str],
                        extra_must: dict | None) -> int:
        done = 0
        for i in range(0, len(property_uuids), TAG_CHUNK):
            chunk = property_uuids[i:i + TAG_CHUNK]
            must = {**(extra_must or {}), "properties": chunk}
            body = {"query": {"must": must, "ordering": ["-list_count"]}, "tags": tags}
            try:
                r = self.api(f"/api/internal/property/{endpoint}/", "POST", body)
                done += (r.get("count") or 0) if isinstance(r, dict) else 0
            except ApiError as e:
                log.warning("  %s chunk skipped: %s", endpoint, str(e)[:120])
            time.sleep(TAG_CHUNK_SLEEP)
        return done

    # ---- SiftLine ----

    def create_siftline_card(self, column_uuid: str, property_uuid: str) -> dict:
        """Confirmed live contract (src/obituary_crm_push.py:329): body key is
        `prop`, not `property`. Creates only -- never moves an existing card
        (a move can fire sequences)."""
        return self.api(f"/api/internal/siftline/board/column/{column_uuid}/card/",
                         "POST", {"prop": property_uuid})

    def property_cards(self, property_uuid: str) -> list[dict]:
        r = self.api(f"/api/internal/siftline/property/{property_uuid}/card/")
        return (r.get("results") or r.get("data") or []) if isinstance(r, dict) else (r or [])

    def delete_siftline_card(self, column_uuid: str, card_uuid: str) -> None:
        """Confirmed live 2026-09-23 (also documented: docs/datasift-api/docs/
        12-endpoint-index.md's destroyCard) -- DELETE .../card/{uuid}/,
        204-style empty body, verified via a property_cards() read-back."""
        self.api(f"/api/internal/siftline/board/column/{column_uuid}/card/{card_uuid}/", "DELETE")

    def board_columns(self, board_uuid: str) -> list[dict]:
        r = self.api(f"/api/internal/siftline/board/{board_uuid}/column/")
        return (r.get("results") or r.get("data") or []) if isinstance(r, dict) else (r or [])

    def boards(self) -> list[dict]:
        r = self.api("/api/internal/siftline/board/")
        return (r.get("results") or r.get("data") or []) if isinstance(r, dict) else (r or [])
