# Quickstart

From zero to your first authenticated DataSift API call in about five minutes.

## 1. Get your Open API key

Open API keys are available on plans above Professional. In the REISift app, go to your account's **integration settings**, then generate a key. The key is a single string with a dot in the middle, for example `AbCd1234.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.

The key acts on behalf of the user it was issued for and inherits that user's permissions. Treat it like a password: keep it secret, never commit it to a repository, and rotate or revoke it from the same screen if it is ever exposed.

## 2. First call - who am I?

```bash
curl https://apiv2.reisift.io/api/internal/user/ \
  -H "Authorization: Api-Key YOUR_OPEN_API_KEY"
```

A 200 response returns your user profile (uuid, name, email, role, feature flags, addons). If you get a 401 or 403, the key is wrong, revoked, or your plan does not include API access.

## 3. Second call - your records

```bash
curl "https://apiv2.reisift.io/api/internal/property/?limit=1" \
  -H "Authorization: Api-Key YOUR_OPEN_API_KEY"
```

Returns `{ "count": <total records>, "data": [ ...one property record... ] }`. The `count` tells you the total size of your account's property database in one cheap call.

## 4. The same thing in Python

```python
import os
import requests

BASE = "https://apiv2.reisift.io"
HEADERS = {
    "Authorization": f"Api-Key {os.environ['DATASIFT_API_KEY']}",
    "Accept": "application/json",
}

me = requests.get(f"{BASE}/api/internal/user/", headers=HEADERS, timeout=30)
me.raise_for_status()
print(me.json()["email"])

props = requests.get(f"{BASE}/api/internal/property/", headers=HEADERS,
                     params={"limit": 5}, timeout=30)
props.raise_for_status()
body = props.json()
print(body["count"], "properties in account")
for p in body["data"]:
    a = p["address"]
    print(p["uuid"], a["street"], a["city"], a["state"])
```

Store the key in an environment variable (`DATASIFT_API_KEY`), never in source code.

## 5. And on SiftMap

```bash
curl "https://map.reisift.io/filters/" \
  -H "Authorization: Api-Key YOUR_OPEN_API_KEY"
```

Lists your saved map filters. The SiftMap API uses the same key and header scheme on its own base URL. See `11-siftmap-api.md`.

## 6. Where to go next

- Working with records: `03-properties.md`
- Building a marketing funnel with tags, lists, and presets: `05-tags-lists-folders.md` and `06-filter-presets.md`
- End-to-end recipes: `13-use-case-playbooks.md`
- Everything that exists, in one table: `12-endpoint-index.md`
