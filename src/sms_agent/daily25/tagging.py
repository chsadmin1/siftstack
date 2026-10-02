"""Read-modify-write tag PATCH -- the only verified-working way to add a tag
to one record over this API.

POST /api/internal/property/{uuid}/add-tags/ is documented but, per
crm_standalone.add_tags's already-verified finding, applies the tag
ACCOUNT-WIDE and ignores its own uuid argument. This mirrors that function's
contract against the JWT-authed Api object instead of crm_standalone's
Api-Key client, since this module is already JWT-authenticated for the
board/task calls.
"""
from __future__ import annotations


def _tag_names(record: dict) -> list[str]:
    return [t.get("name") if isinstance(t, dict) else str(t) for t in (record.get("tags") or [])]


def add_tags(api, property_uuid: str, tags: list[str]) -> dict:
    current = _tag_names(api.call(f"/api/internal/property/{property_uuid}/"))
    merged = current + [t for t in tags if t not in current]
    return api.call(
        f"/api/internal/property/{property_uuid}/", method="PATCH", body={"tags": merged}
    )
