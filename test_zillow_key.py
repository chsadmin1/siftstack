"""Quick test: verify OpenWeb Ninja API key works."""
import sys
sys.path.insert(0, "src")

import config
import requests

api_key = config.OPENWEBNINJA_API_KEY
print("Key present:", bool(api_key))

resp = requests.get(
    "https://api.openwebninja.com/realtime-zillow-data/property-details-address",
    headers={"x-api-key": api_key},
    params={"address": "4342 Jarboe St Kansas City MO 64111"},
    timeout=30,
)
print("Status:", resp.status_code)
if resp.ok:
    d = resp.json()
    print("Zestimate:", d.get("zestimate"))
    print("Bedrooms:", d.get("bedrooms"))
    print("Bathrooms:", d.get("bathrooms"))
    print("SqFt:", d.get("livingAreaValue"))
    print("Home type:", d.get("homeType"))
    print("Home status:", d.get("homeStatus"))
    print("Last sold price:", d.get("lastSoldPrice"))
    print("Last sold date:", d.get("dateSoldString"))
else:
    print("Error:", resp.text[:300])
