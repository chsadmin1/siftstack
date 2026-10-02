"""Distressed MLS listing scanner for SiftStack.

Two-stage pipeline:
  Stage 1 (fast screen): Filter all ZIP listings using signals available
    directly from the search result — no extra API calls.
  Stage 2 (full score): Run Two-Bucket ARV via comp_analyzer + FEMA flood
    zone check on Stage 1 candidates. Score >= LISTING_MIN_TRIGGER_SCORE
    with at least one Price/Value signal → qualify for output.

Change detection: listing_state.json persists watchlist + ARV cache across
runs. Use --watch to emit only delta records (newly qualified, DOM milestones,
price drops, back-on-market returns).

NOTE on the search endpoint: OpenWebNinja's Zillow API search endpoint is
  GET https://api.openwebninja.com/realtime-zillow-data/search
with params: location (ZIP or city), home_type, page.
Verify this against your RapidAPI subscription — the endpoint slug may vary.
"""

import csv
import json
import logging
import math
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

import config
from config import OUTPUT_DIR, PROJECT_ROOT, load_state, save_state
from notice_parser import NoticeData

logger = logging.getLogger(__name__)

# ── API endpoints ────────────────────────────────────────────────────
_API_BASE = "https://api.openwebninja.com/realtime-zillow-data"
SEARCH_ENDPOINT = f"{_API_BASE}/search"
PROPERTY_ENDPOINT = f"{_API_BASE}/property-details-address"
# FEMA National Flood Hazard Layer — flood zone layer (layer 28)
FEMA_ENDPOINT = (
    "https://hazards.fema.gov/arcgis/rest/services/public/NFHL"
    "/MapServer/28/query"
)

# Flood zones where conventional financing is restricted → opportunity
_FEMA_HIGH_RISK = {"A", "AE", "AH", "AO", "AR", "VE", "V"}
_FEMA_HIGH_RISK_PREFIX = ("A", "V")

# Approximate prevailing 30yr mortgage rates by purchase year range
# Used for equity estimation from last-sale-price + years held
_RATE_BY_ERA: list[tuple[range, float]] = [
    (range(2000, 2005), 0.065),
    (range(2005, 2009), 0.062),
    (range(2009, 2013), 0.047),
    (range(2013, 2017), 0.040),
    (range(2017, 2020), 0.043),
    (range(2020, 2022), 0.031),
    (range(2022, 2024), 0.065),
    (range(2024, 2028), 0.070),
]


# ── Data structures ───────────────────────────────────────────────────

@dataclass
class ListingRecord:
    """Parsed data from a Zillow API active listing."""
    zpid: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    county: str = ""
    latitude: float = 0.0
    longitude: float = 0.0

    # Pricing
    list_price: float = 0.0
    zestimate: float = 0.0
    price_per_sqft: float = 0.0

    # Property details
    bedrooms: int = 0
    bathrooms: float = 0.0
    sqft: int = 0
    year_built: int = 0
    property_type: str = ""

    # Market data
    dom: int = 0
    original_list_price: float = 0.0
    reduction_count: int = 0
    total_reduction_pct: float = 0.0
    largest_reduction_pct: float = 0.0
    avg_days_between_reductions: float = 0.0

    # Status
    home_status: str = ""
    listing_sub_type: str = ""
    back_on_market: bool = False
    fsbo: bool = False

    # Listing meta
    owner_name: str = ""
    listing_agent: str = ""
    open_house_count: int = 0
    description: str = ""
    source_url: str = ""

    # Sale history
    last_sale_price: float = 0.0
    last_sale_date: str = ""

    # Stage 2 computed
    arv_estimate: float = 0.0
    arv_discount_pct: float = 0.0
    zip_median_ppsf: float = 0.0
    ppsf_discount_pct: float = 0.0
    zestimate_discount_pct: float = 0.0
    tax_assessed_value: float = 0.0
    tax_discount_pct: float = 0.0
    equity_pct: float = 0.0
    flood_zone: str = ""

    # Scoring
    trigger_score: int = 0
    trigger_flags: list = field(default_factory=list)

    # Watchlist metadata
    first_seen_date: str = ""
    last_updated: str = ""


# ── API helpers ───────────────────────────────────────────────────────

# Raised when we hit a persistent rate limit (429 exhausted retries)
class RateLimitExhausted(Exception):
    pass


def _api_get(endpoint: str, params: dict, api_key: str, timeout: int = 30) -> dict | list | None:
    """Authenticated GET to OpenWebNinja API with retry on rate-limit.

    Raises RateLimitExhausted if all retries return 429, so the caller can
    implement a circuit breaker instead of burning 3 min per ZIP.
    """
    headers = {"x-api-key": api_key}
    for attempt in range(1, 3):
        try:
            resp = requests.get(endpoint, headers=headers, params=params, timeout=timeout)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                if attempt == 2:
                    # Both retries exhausted — surface this so the caller can abort
                    raise RateLimitExhausted(
                        "API quota exceeded. Wait ~1 hour then retry."
                    )
                wait = 30  # shorter wait — if quota window > 3 min, longer waits don't help
                logger.warning("Rate limit hit — waiting %ds (attempt %d/2)", wait, attempt)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            body = resp.json()
            if isinstance(body, list):
                return body
            if isinstance(body, dict):
                if body.get("status") == "OK" and body.get("data"):
                    return body["data"]
                return body
            return None
        except RateLimitExhausted:
            raise
        except requests.Timeout:
            logger.warning("API timeout (attempt %d/2)", attempt)
        except requests.RequestException as e:
            logger.warning("API error: %s (attempt %d/2)", e, attempt)
    return None


def _search_listings(zip_code: str, api_key: str) -> list[dict]:
    """Fetch active for-sale listings in a ZIP code via OpenWebNinja Zillow API.

    Paginates until all results are collected. Response shape varies by API
    version — handles both list-direct and wrapped {results: [...]} responses.
    Propagates RateLimitExhausted so the caller can abort early.
    """
    all_listings: list[dict] = []
    page = 1

    while True:
        params = {
            "location": zip_code,
            "home_type": "Houses",
            "page": page,
        }
        data = _api_get(SEARCH_ENDPOINT, params, api_key)  # may raise RateLimitExhausted
        if not data:
            break

        if isinstance(data, list):
            results = data
            total = 0
        else:
            results = (
                data.get("results")
                or data.get("listings")
                or data.get("props")
                or data.get("data")
                or []
            )
            total = int(data.get("totalResultCount") or data.get("total") or 0)

        if not results:
            break

        all_listings.extend(results)

        # Stop when we have everything or hit an implicit last page
        if total and len(all_listings) >= total:
            break
        if not isinstance(data, list) and not data.get("nextPage") and len(results) < 20:
            break
        if len(results) < 20:
            break

        page += 1
        time.sleep(random.uniform(1.5, 2.5))

    return all_listings


def _check_flood_zone(lat: float, lon: float) -> str:
    """Query FEMA NFHL for flood zone designation at a lat/lon point."""
    if not lat or not lon:
        return ""
    try:
        params = {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "FLD_ZONE,ZONE_SUBTY",
            "returnGeometry": "false",
            "f": "json",
        }
        resp = requests.get(FEMA_ENDPOINT, params=params, timeout=15)
        if resp.status_code != 200:
            return ""
        features = resp.json().get("features") or []
        if not features:
            return "X"  # Zone X = minimal flood risk (outside floodplain)
        attrs = features[0].get("attributes") or {}
        return (attrs.get("FLD_ZONE") or "").upper().strip()
    except Exception as e:
        logger.debug("FEMA flood zone lookup failed: %s", e)
        return ""


# ── Listing parsing ───────────────────────────────────────────────────

def _parse_price_history(history: list) -> dict:
    """Extract reduction signals from Zillow price history array."""
    if not history:
        return {}

    dated = [h for h in history if h.get("price") and h.get("date")]
    if not dated:
        return {}

    dated_sorted = sorted(dated, key=lambda x: str(x.get("date", "")), reverse=True)

    # Original list price = oldest listing event
    listing_events = [
        h for h in dated_sorted
        if "list" in (h.get("event") or "").lower()
    ]
    original_price = float(listing_events[-1].get("price", 0)) if listing_events else 0.0

    # Identify price cut events
    reductions = [
        h for h in dated_sorted
        if any(k in (h.get("event") or "").lower()
               for k in ("price cut", "price reduction", "reduced", "price change"))
        and float(h.get("price") or 0) < (float(dated_sorted[dated_sorted.index(h) + 1].get("price", 0))
                                           if dated_sorted.index(h) + 1 < len(dated_sorted) else float("inf"))
    ]

    if not reductions or not original_price:
        return {
            "original_price": original_price,
            "reduction_count": len(reductions),
            "total_reduction_pct": 0.0,
            "largest_reduction_pct": 0.0,
            "avg_days_between_reductions": 0.0,
        }

    current_price = float(dated_sorted[0].get("price", 0))
    total_drop_pct = (original_price - current_price) / original_price * 100 if original_price else 0.0

    # Largest single drop — compare consecutive prices
    prices = [original_price] + [float(r.get("price", 0)) for r in reversed(reductions)]
    drops = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0:
            drops.append((prices[i - 1] - prices[i]) / prices[i - 1] * 100)
    largest = max(drops) if drops else 0.0

    # Average days between consecutive reductions
    avg_days = 0.0
    if len(reductions) >= 2:
        dates = []
        for r in reductions:
            try:
                dates.append(datetime.strptime(str(r["date"])[:10], "%Y-%m-%d"))
            except ValueError:
                pass
        if len(dates) >= 2:
            gaps = [(dates[i] - dates[i + 1]).days for i in range(len(dates) - 1)]
            avg_days = sum(gaps) / len(gaps) if gaps else 0.0

    return {
        "original_price": original_price,
        "reduction_count": len(reductions),
        "total_reduction_pct": round(total_drop_pct, 1),
        "largest_reduction_pct": round(largest, 1),
        "avg_days_between_reductions": round(avg_days, 0),
    }


def _estimate_equity(list_price: float, last_sale_price: float, last_sale_date: str) -> float:
    """Estimate owner equity % using last sale + amortization approximation."""
    if not last_sale_price or not last_sale_date:
        return 0.0
    try:
        purchase_year = int(last_sale_date[:4])
        years_held = max(0, date.today().year - purchase_year)
        months_paid = min(years_held * 12, 360)

        annual_rate = 0.055  # default
        for yr_range, r in _RATE_BY_ERA:
            if purchase_year in yr_range:
                annual_rate = r
                break

        mr = annual_rate / 12
        n = 360

        if mr > 0 and months_paid > 0:
            payment = last_sale_price * (mr * (1 + mr) ** n) / ((1 + mr) ** n - 1)
            remaining = last_sale_price * ((1 + mr) ** n - (1 + mr) ** months_paid) / ((1 + mr) ** n - 1)
            equity = list_price - remaining
            return round(equity / list_price * 100, 1) if list_price > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        pass
    return 0.0


def _parse_zillow_listing(raw: dict, county: str, zip_code: str) -> ListingRecord | None:
    """Parse a raw Zillow API listing dict into a ListingRecord."""
    status = (raw.get("homeStatus") or raw.get("status") or "").upper()
    # Keep only active for-sale listings
    if status and "SALE" not in status and "ACTIVE" not in status and "COMING" not in status:
        return None

    price = float(raw.get("price") or raw.get("unformattedPrice") or 0)
    if not price or price < 10_000:
        return None

    sqft = int(raw.get("livingArea") or raw.get("sqft") or 0)
    ppsf = round(price / sqft, 2) if sqft > 0 else 0.0

    history = raw.get("priceHistory") or []
    hist = _parse_price_history(history)

    # Detect Back on Market (prior Pending/Under Contract → now Active)
    back_on_market = False
    prior_status = ""
    for entry in history:
        ev = (entry.get("event") or "").lower()
        if "pending" in ev or "under contract" in ev or "contract" in ev:
            back_on_market = True
            prior_status = "pending"
            break

    # FSBO — listingType is always a plain string when present
    listing_type_raw = raw.get("listingType") or ""
    listing_type = listing_type_raw.lower() if isinstance(listing_type_raw, str) else ""
    # marketingStatusSimplifiedCd is also a reliable FSBO signal
    mktg = (raw.get("marketingStatusSimplifiedCd") or "").lower()
    fsbo = "fsbo" in listing_type or "for sale by owner" in listing_type or "by owner" in mktg

    # REO / distressed sub-type — API returns listingSubType as a dict of boolean flags
    sub_type_raw = raw.get("listingSubType")
    if isinstance(sub_type_raw, dict):
        # Map boolean flags to canonical string labels (checked in priority order)
        if sub_type_raw.get("isForeclosure") or sub_type_raw.get("is_foreclosure"):
            sub_type = "foreclosure"
        elif sub_type_raw.get("isBankOwned") or sub_type_raw.get("is_bankOwned") or sub_type_raw.get("isReo"):
            sub_type = "reo"
        elif sub_type_raw.get("isForAuction") or sub_type_raw.get("is_auction"):
            sub_type = "auction"
        elif sub_type_raw.get("isShortSale") or sub_type_raw.get("is_shortSale"):
            sub_type = "short_sale"
        elif sub_type_raw.get("isComingSoon") or sub_type_raw.get("is_comingSoon"):
            sub_type = "coming_soon"
        else:
            sub_type = ""
    elif isinstance(sub_type_raw, str):
        sub_type = sub_type_raw.lower()
    else:
        sub_type = ""

    # Last sale from history
    last_sale_price, last_sale_date = 0.0, ""
    for entry in history:
        ev = (entry.get("event") or "").lower()
        if ev in ("sold", "listed (sold)"):
            last_sale_price = float(entry.get("price") or 0)
            last_sale_date = str(entry.get("date", ""))[:10]
            break

    equity_pct = _estimate_equity(price, last_sale_price, last_sale_date)

    zpid = str(raw.get("zpid") or raw.get("id") or "")
    now = datetime.now().strftime("%Y-%m-%d")

    return ListingRecord(
        zpid=zpid,
        address=raw.get("streetAddress") or raw.get("addressStreet") or raw.get("address") or "",
        city=raw.get("city") or "",
        state=raw.get("state") or "",
        zip_code=raw.get("zipcode") or raw.get("zip") or zip_code,
        county=county,
        latitude=float(raw.get("latitude") or 0),
        longitude=float(raw.get("longitude") or 0),
        list_price=price,
        zestimate=float(raw.get("zestimate") or 0),
        price_per_sqft=ppsf,
        bedrooms=int(raw.get("bedrooms") or 0),
        bathrooms=float(raw.get("bathrooms") or 0),
        sqft=sqft,
        year_built=int(raw.get("yearBuilt") or 0),
        property_type=raw.get("homeType") or "",
        dom=int(raw.get("daysOnZillow") or raw.get("daysOnMarket") or 0),
        original_list_price=hist.get("original_price", price),
        reduction_count=hist.get("reduction_count", 0),
        total_reduction_pct=hist.get("total_reduction_pct", 0.0),
        largest_reduction_pct=hist.get("largest_reduction_pct", 0.0),
        avg_days_between_reductions=hist.get("avg_days_between_reductions", 0.0),
        home_status=status,
        listing_sub_type=sub_type,
        back_on_market=back_on_market,
        fsbo=fsbo,
        listing_agent=raw.get("brokerName") or raw.get("listingAgentName") or "",
        open_house_count=len(raw.get("openHouseSchedule") or []),
        description=raw.get("description") or "",
        source_url=(
            f"https://www.zillow.com/homedetails/{zpid}_zpid/"
            if zpid else raw.get("detailUrl") or ""
        ),
        last_sale_price=last_sale_price,
        last_sale_date=last_sale_date,
        equity_pct=equity_pct,
        first_seen_date=now,
        last_updated=now,
    )


# ── Stage 1: Fast screen ─────────────────────────────────────────────

def _keyword_hit(description: str) -> bool:
    text = description.lower()
    return any(kw in text for kw in config.LISTING_KEYWORD_SIGNALS)


def _is_distressed_subtype(listing: ListingRecord) -> bool:
    sub = listing.listing_sub_type.lower()
    return any(k in sub for k in ("reo", "foreclosure", "hud", "bank", "auction", "short_sale", "short sale"))


def stage1_screen(listing: ListingRecord) -> bool:
    """Return True if any fast signal fires — advance to Stage 2."""
    if listing.reduction_count >= 1:
        return True
    if listing.dom >= config.LISTING_MIN_DOM:
        return True
    if _keyword_hit(listing.description):
        return True
    if _is_distressed_subtype(listing):
        return True
    if listing.back_on_market:
        return True
    if listing.fsbo:
        return True
    if (listing.zestimate > 0
            and listing.list_price < listing.zestimate * (1 - config.LISTING_ZESTIMATE_DISCOUNT_PCT / 100)):
        return True
    if 0 < listing.price_per_sqft < config.LISTING_MIN_PRICE_FLOOR_PPSF:
        return True
    return False


# ── Stage 2: Full scoring ────────────────────────────────────────────

def _zip_median_ppsf(all_listings: list[ListingRecord], zip_code: str) -> float:
    """Compute median price/sqft from all listings pulled for this ZIP."""
    values = [l.price_per_sqft for l in all_listings if l.zip_code == zip_code and l.price_per_sqft > 0]
    if not values:
        return 0.0
    values.sort()
    n = len(values)
    return (values[n // 2 - 1] + values[n // 2]) / 2 if n % 2 == 0 else values[n // 2]


def _get_arv(listing: ListingRecord, arv_cache: dict, api_key: str) -> float:
    """Estimate ARV from neighborhood Zestimate comps via property-details-address.

    The /similar-sale-homes comps endpoint is unavailable on the current API tier,
    so ARV is estimated as: median PPSF of nearbyHomes × subject sqft.
    nearbyHomes prices are Zillow Zestimates for adjacent off-market properties —
    a reasonable proxy for what the block supports at full market value.
    """
    zpid = listing.zpid
    cached = arv_cache.get(zpid, {})
    if cached.get("cached_at"):
        try:
            age = (datetime.now() - datetime.strptime(cached["cached_at"], "%Y-%m-%d")).days
            if age < config.LISTING_ARV_CACHE_DAYS:
                return float(cached.get("arv", 0))
        except ValueError:
            pass

    try:
        # Comma-separated format required — space-joined returns no data from this endpoint
        loc = f"{listing.city}, {listing.state} {listing.zip_code}".strip()
        full_address = f"{listing.address}, {loc}" if loc else listing.address

        time.sleep(random.uniform(1.5, 2.5))
        data = _api_get(PROPERTY_ENDPOINT, {"address": full_address}, api_key)
        if not data:
            return 0.0

        subject_sqft = int(data.get("livingArea") or listing.sqft or 0)
        subject_type = (data.get("homeType") or "").upper()

        nearby = data.get("nearbyHomes") or []
        valid_ppsf: list[float] = []

        for h in nearby:
            if not isinstance(h, dict):
                continue
            h_price = float(h.get("price") or 0)
            h_sqft = int(h.get("livingArea") or 0)
            h_type = (h.get("homeType") or "").upper()
            h_status = (h.get("homeStatus") or "").upper()

            if h_price < 20_000 or h_sqft < 400:
                continue  # outlier or missing data
            if h_status == "FOR_SALE":
                continue  # skip active listings — could be distressed
            if subject_type and h_type and subject_type != h_type:
                continue  # skip different property types (e.g. condo vs SFR)

            ppsf = h_price / h_sqft
            if 50.0 < ppsf < 600.0:  # sanity-check PPSF range
                valid_ppsf.append(ppsf)

        if len(valid_ppsf) < 2:
            return 0.0

        valid_ppsf.sort()
        n = len(valid_ppsf)
        median_ppsf = (valid_ppsf[n // 2 - 1] + valid_ppsf[n // 2]) / 2 if n % 2 == 0 else valid_ppsf[n // 2]

        if not subject_sqft:
            return 0.0

        arv = round(median_ppsf * subject_sqft)
        arv_cache[zpid] = {"arv": arv, "cached_at": datetime.now().strftime("%Y-%m-%d")}
        logger.debug(
            "ARV %s: %d nearby comps, median $%.0f/sqft × %d sqft = $%d",
            listing.address, len(valid_ppsf), median_ppsf, subject_sqft, arv,
        )
        return float(arv)
    except Exception as e:
        logger.warning("ARV computation failed for %s: %s", listing.address, e)
        return 0.0


def score_listing(
    listing: ListingRecord,
    all_listings: list[ListingRecord],
    arv_cache: dict,
    api_key: str,
    check_flood: bool = True,
) -> ListingRecord:
    """Run Stage 2 full scoring. Mutates listing.trigger_score and .trigger_flags."""
    flags: list[str] = []
    score = 0

    # ── ZIP median $/sqft ──────────────────────────────────────────
    zip_med = _zip_median_ppsf(all_listings, listing.zip_code)
    listing.zip_median_ppsf = zip_med
    if zip_med > 0 and listing.price_per_sqft > 0:
        disc = (zip_med - listing.price_per_sqft) / zip_med * 100
        listing.ppsf_discount_pct = round(disc, 1)
        if disc >= config.LISTING_PPSF_DISCOUNT_PCT:
            score += 1
            flags.append("ppsf_discount")

    # ── Zestimate discount ─────────────────────────────────────────
    if listing.zestimate > 0:
        z_disc = (listing.zestimate - listing.list_price) / listing.zestimate * 100
        listing.zestimate_discount_pct = round(z_disc, 1)
        if z_disc >= config.LISTING_ZESTIMATE_DISCOUNT_PCT:
            score += 1
            flags.append("zestimate_discount")

    # ── Two-Bucket ARV ─────────────────────────────────────────────
    arv = _get_arv(listing, arv_cache, api_key)
    listing.arv_estimate = arv
    if arv > 0:
        arv_disc = (arv - listing.list_price) / arv * 100
        listing.arv_discount_pct = round(arv_disc, 1)
        if arv_disc >= config.LISTING_MIN_DISCOUNT_PCT:
            score += 1
            flags.append("arv_discount")

    # ── Price reduction signals ────────────────────────────────────
    if listing.reduction_count >= 1:
        score += 1
        flags.append("price_reduction")
    if listing.reduction_count >= 2:
        score += 1
        flags.append("multiple_reductions")
    if listing.largest_reduction_pct >= 10:
        score += 1
        flags.append("large_single_reduction")

    # ── DOM ────────────────────────────────────────────────────────
    if listing.dom >= config.LISTING_MIN_DOM:
        score += 1
        flags.append("dom_60")
    if listing.dom >= 90:
        score += 1
        flags.append("dom_90")

    # ── Back on market ─────────────────────────────────────────────
    if listing.back_on_market:
        score += 1
        flags.append("back_on_market")

    # ── Distress keyword ───────────────────────────────────────────
    if _keyword_hit(listing.description):
        score += 1
        flags.append("keyword_match")

    # ── REO / distressed sub-type ──────────────────────────────────
    if _is_distressed_subtype(listing):
        score += 1
        flags.append("reo_distressed")

    # ── FSBO ───────────────────────────────────────────────────────
    if listing.fsbo:
        score += 1
        flags.append("fsbo")

    # ── Price floor ────────────────────────────────────────────────
    if 0 < listing.price_per_sqft < config.LISTING_MIN_PRICE_FLOOR_PPSF:
        score += 1
        flags.append("below_floor_ppsf")

    # ── Old structure (deferred maintenance proxy) ─────────────────
    if 0 < listing.year_built < config.LISTING_MAX_YEAR_BUILT_OLD:
        score += 1
        flags.append("old_structure")

    # ── Passive seller (high DOM + no open houses) ─────────────────
    if listing.dom >= config.LISTING_MIN_DOM and listing.open_house_count == 0:
        score += 1
        flags.append("no_showings_high_dom")

    # ── Equity signals ─────────────────────────────────────────────
    if listing.equity_pct > 60:
        score += 1
        flags.append("high_equity")
    elif 0 < listing.equity_pct < 10:
        score += 1
        flags.append("low_equity_pressure")

    # ── FEMA flood zone ────────────────────────────────────────────
    if check_flood and listing.latitude and listing.longitude:
        zone = _check_flood_zone(listing.latitude, listing.longitude)
        listing.flood_zone = zone
        if zone and (zone in _FEMA_HIGH_RISK or any(zone.startswith(p) for p in _FEMA_HIGH_RISK_PREFIX)):
            score += 1
            flags.append(f"flood_zone_{zone}")

    listing.trigger_score = score
    listing.trigger_flags = flags
    return listing


# ── Portfolio liquidation detection ──────────────────────────────────

def _tag_portfolio_liquidations(listings: list[ListingRecord]) -> None:
    """Flag listings where the same owner/LLC has 2+ active listings."""
    owner_idx: dict[str, list[int]] = {}
    for i, l in enumerate(listings):
        key = l.owner_name.strip().lower()
        if key:
            owner_idx.setdefault(key, []).append(i)
    for indices in owner_idx.values():
        if len(indices) >= 2:
            for i in indices:
                if "portfolio_liquidation" not in listings[i].trigger_flags:
                    listings[i].trigger_flags.append("portfolio_liquidation")
                    listings[i].trigger_score += 1


# ── Double-distressed cross-reference ────────────────────────────────

def _load_recent_notice_addresses(days_back: int = 30) -> set[str]:
    """Return normalized addresses from output CSVs within the last N days."""
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    addresses: set[str] = set()
    if not OUTPUT_DIR.exists():
        return addresses
    for csv_path in OUTPUT_DIR.glob("*.csv"):
        try:
            mtime = datetime.fromtimestamp(csv_path.stat().st_mtime).strftime("%Y-%m-%d")
            if mtime < cutoff:
                continue
            with open(csv_path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    addr = (row.get("address") or "").strip().lower()
                    if addr:
                        addresses.add(addr)
        except Exception:
            pass
    return addresses


def _tag_double_distressed(listings: list[ListingRecord], known: set[str]) -> None:
    """Flag + weight listings whose address matches an existing notice."""
    for l in listings:
        if l.address.strip().lower() in known:
            if "double_distressed" not in l.trigger_flags:
                l.trigger_flags.append("double_distressed")
                l.trigger_score += 2  # Highest-priority signal


# ── Watchlist / change detection ─────────────────────────────────────

def _load_state() -> dict:
    return load_state(config.LISTING_STATE_FILE)


def _save_listing_state(state: dict) -> None:
    save_state(config.LISTING_STATE_FILE, state)


def _detect_changes(listing: ListingRecord, state: dict) -> list[str]:
    """Compare current listing to persisted watchlist entry. Return event list."""
    prior = state.get("watchlist", {}).get(listing.zpid)
    if not prior:
        return []
    changes = []

    if listing.list_price < prior.get("last_price", listing.list_price) * 0.99:
        changes.append("price_drop")

    fired = set(prior.get("dom_milestones_fired", []))
    for ms in (60, 90, 120):
        if listing.dom >= ms and str(ms) not in fired:
            changes.append(f"dom_{ms}_crossed")

    prev_status = prior.get("last_status", "")
    if "PENDING" in prev_status.upper() and "ACTIVE" in listing.home_status.upper():
        changes.append("back_on_market")

    prev_score = prior.get("trigger_score", 0)
    if listing.trigger_score >= config.LISTING_MIN_TRIGGER_SCORE > prev_score:
        changes.append("newly_qualified")

    return changes


def _update_watchlist_entry(listing: ListingRecord, state: dict) -> None:
    if "watchlist" not in state:
        state["watchlist"] = {}
    prior = state["watchlist"].get(listing.zpid, {})
    fired = set(prior.get("dom_milestones_fired", []))
    for ms in (60, 90, 120):
        if listing.dom >= ms:
            fired.add(str(ms))
    state["watchlist"][listing.zpid] = {
        "address": listing.address,
        "city": listing.city,
        "county": listing.county,
        "last_price": listing.list_price,
        "last_dom": listing.dom,
        "last_status": listing.home_status,
        "trigger_score": listing.trigger_score,
        "trigger_flags": listing.trigger_flags,
        "first_seen_date": prior.get("first_seen_date", listing.first_seen_date),
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
        "dom_milestones_fired": list(fired),
    }


# ── Output conversion ─────────────────────────────────────────────────

_PRICE_FLAGS = frozenset({
    "arv_discount", "ppsf_discount", "zestimate_discount", "tax_discount",
    "price_reduction", "multiple_reductions", "large_single_reduction",
})


def _has_price_signal(listing: ListingRecord) -> bool:
    return bool(set(listing.trigger_flags) & _PRICE_FLAGS)


def listing_to_notice_data(listing: ListingRecord, mode: str = "below-market") -> NoticeData:
    """Convert a scored ListingRecord to NoticeData for CSV + DataSift upload."""
    notice_type = "mls_listing" if mode == "below-market" else "stale_listing"

    tags = [
        "mls_listing",
        listing.county.lower(),
        datetime.now().strftime("%Y-%m"),
    ] + listing.trigger_flags

    notes = "|".join([
        f"list_price={int(listing.list_price)}",
        f"zestimate={int(listing.zestimate)}",
        f"arv_estimate={int(listing.arv_estimate)}",
        f"arv_discount_pct={listing.arv_discount_pct:.1f}",
        f"ppsf={listing.price_per_sqft:.0f}",
        f"zip_median_ppsf={listing.zip_median_ppsf:.0f}",
        f"ppsf_discount_pct={listing.ppsf_discount_pct:.1f}",
        f"equity_pct={listing.equity_pct:.0f}",
        f"dom={listing.dom}",
        f"reduction_count={listing.reduction_count}",
        f"total_reduction_pct={listing.total_reduction_pct:.1f}",
        f"largest_reduction_pct={listing.largest_reduction_pct:.1f}",
        f"trigger_score={listing.trigger_score}",
        f"triggers={','.join(listing.trigger_flags)}",
        f"back_on_market={listing.back_on_market}",
        f"fsbo={listing.fsbo}",
        f"flood_zone={listing.flood_zone}",
        f"last_sale_price={int(listing.last_sale_price)}",
        f"last_sale_date={listing.last_sale_date}",
        f"beds={listing.bedrooms}",
        f"baths={listing.bathrooms}",
        f"sqft={listing.sqft}",
        f"year_built={listing.year_built}",
        f"desc={listing.description[:500]}",
    ])

    return NoticeData(
        date_added=datetime.now().strftime("%Y-%m-%d"),
        address=listing.address,
        city=listing.city,
        state=listing.state,
        zip=listing.zip_code,
        owner_name=listing.owner_name or listing.listing_agent,
        notice_type=notice_type,
        county=listing.county,
        source_url=listing.source_url,
        mls_status="Active",
        mls_listing_price=str(int(listing.list_price)),
        estimated_value=str(int(listing.zestimate)) if listing.zestimate else "",
        mls_last_sold_price=str(int(listing.last_sale_price)) if listing.last_sale_price else "",
        mls_last_sold_date=listing.last_sale_date,
        bedrooms=str(listing.bedrooms) if listing.bedrooms else "",
        bathrooms=str(listing.bathrooms) if listing.bathrooms else "",
        sqft=str(listing.sqft) if listing.sqft else "",
        year_built=str(listing.year_built) if listing.year_built else "",
        latitude=str(listing.latitude) if listing.latitude else "",
        longitude=str(listing.longitude) if listing.longitude else "",
        raw_text=notes,
    )


# ── Slack summary ─────────────────────────────────────────────────────

def build_listing_slack_summary(
    newly_qualified: list[ListingRecord],
    watchlist_updates: dict,
    *,
    total_scanned: int = 0,
    stage1_passed: int = 0,
    elapsed_min: float = 0.0,
) -> str:
    lines = [
        f"*SiftStack MLS Scan ({datetime.now().strftime('%Y-%m-%d')})*",
        "",
        (
            f"*Scanned:* {total_scanned} listings | "
            f"Stage 1: {stage1_passed} | "
            f"New deals: {len(newly_qualified)}"
        ),
    ]

    if newly_qualified:
        lines += ["", f"*Newly Qualified ({len(newly_qualified)}):*"]
        for l in newly_qualified[:10]:
            disc = f"{l.arv_discount_pct:.0f}% below ARV" if l.arv_discount_pct > 0 else f"DOM {l.dom}d"
            lines.append(
                f"  {l.address}, {l.city} | ${l.list_price:,.0f} | {disc} | score={l.trigger_score}"
            )
        if len(newly_qualified) > 10:
            lines.append(f"  … and {len(newly_qualified) - 10} more")

    price_drops = watchlist_updates.get("price_drops", [])
    dom_hits = watchlist_updates.get("dom_milestones", [])
    bom = watchlist_updates.get("back_on_market", [])

    if price_drops:
        lines += ["", f"*Price Drops ({len(price_drops)}):*"]
        for item in price_drops[:5]:
            lines.append(
                f"  {item['address']} | ${item['old']:,.0f} → ${item['new']:,.0f}"
            )

    if dom_hits:
        lines += ["", f"*DOM Milestones ({len(dom_hits)}):*"]
        for item in dom_hits[:5]:
            lines.append(f"  {item['address']} | {item['dom']}d on market")

    if bom:
        lines += ["", f"*Back on Market ({len(bom)}):*"]
        for addr in bom[:5]:
            lines.append(f"  {addr}")

    if elapsed_min > 0:
        lines += ["", f"Pipeline: {elapsed_min:.0f} min"]

    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────

def run_listing_scan(
    counties: list[str] | None = None,
    min_discount_pct: float | None = None,
    min_dom: int | None = None,
    min_score: int | None = None,
    mode: str = "both",
    watch_mode: bool = False,
    check_flood: bool = True,
    api_key: str = "",
    notify_slack: bool = False,
    max_listings: int = 0,
) -> tuple[list[NoticeData], dict]:
    """Run the listing scan pipeline.

    Args:
        counties: County names to scan (None = all configured counties).
        min_discount_pct: Minimum ARV discount % to qualify (default from config).
        min_dom: Minimum days on market for stale mode (default from config).
        min_score: Minimum trigger score to qualify (default from config).
        mode: "below-market", "stale", or "both".
        watch_mode: If True, emit only delta records vs last run.
        check_flood: Whether to query FEMA flood zone API.
        api_key: OpenWebNinja API key (defaults to config.OPENWEBNINJA_API_KEY).
        notify_slack: Send Slack summary after run.

    Returns:
        (notices, run_stats)
    """
    api_key = api_key or config.OPENWEBNINJA_API_KEY
    if not api_key:
        logger.error("OPENWEBNINJA_API_KEY not set — cannot run listing scan")
        return [], {}

    _min_discount = min_discount_pct if min_discount_pct is not None else config.LISTING_MIN_DISCOUNT_PCT
    _min_dom = min_dom if min_dom is not None else config.LISTING_MIN_DOM
    _min_score = min_score if min_score is not None else config.LISTING_MIN_TRIGGER_SCORE

    # Override thresholds temporarily for scoring functions
    config.LISTING_MIN_DISCOUNT_PCT = _min_discount
    config.LISTING_MIN_DOM = _min_dom
    config.LISTING_MIN_TRIGGER_SCORE = _min_score

    start_time = datetime.now()
    state = _load_state()
    arv_cache = state.get("arv_cache", {})
    known_addresses = _load_recent_notice_addresses(days_back=30)

    # Build ZIP → county map
    target_counties = [c.title() for c in (counties or list(config.LISTING_ZIP_CODES.keys()))]
    zip_county: dict[str, str] = {}
    for county in target_counties:
        for z in config.LISTING_ZIP_CODES.get(county, []):
            zip_county[z] = county

    if not zip_county:
        logger.error("No ZIP codes configured for: %s", target_counties)
        return [], {}

    logger.info("Scanning %d ZIPs across %s", len(zip_county), ", ".join(target_counties))

    # ── Pre-flight API check ───────────────────────────────────────
    first_zip = next(iter(zip_county))
    try:
        _api_get(SEARCH_ENDPOINT, {"location": first_zip, "home_type": "Houses", "page": 1}, api_key)
    except RateLimitExhausted:
        logger.error(
            "API rate limit active before scan started. "
            "Wait ~1 hour for the quota window to reset, then re-run."
        )
        return [], {"error": "rate_limit", "total_scanned": 0, "stage1_passed": 0,
                    "qualifying": 0, "dedup_skipped": 0, "to_upload": 0}

    # ── Pull all active listings ──────────────────────────────────
    all_listings: list[ListingRecord] = []
    consecutive_rate_limit_zips = 0
    CIRCUIT_BREAKER_THRESHOLD = 3  # abort after 3 consecutive rate-limited ZIPs

    for zip_code, county in zip_county.items():
        logger.info("ZIP %s (%s)...", zip_code, county)
        try:
            raw_batch = _search_listings(zip_code, api_key)
            consecutive_rate_limit_zips = 0  # reset on success
            for raw in raw_batch:
                rec = _parse_zillow_listing(raw, county, zip_code)
                if rec:
                    all_listings.append(rec)
            time.sleep(random.uniform(6.0, 10.0))  # ~7 req/min stays under most plan limits
        except RateLimitExhausted:
            consecutive_rate_limit_zips += 1
            logger.warning(
                "Rate limit hit on ZIP %s (%d/%d consecutive)",
                zip_code, consecutive_rate_limit_zips, CIRCUIT_BREAKER_THRESHOLD,
            )
            if consecutive_rate_limit_zips >= CIRCUIT_BREAKER_THRESHOLD:
                logger.error(
                    "Circuit breaker: %d consecutive rate-limited ZIPs. "
                    "Aborting scan — quota window exhausted. Wait ~1 hour and retry.",
                    CIRCUIT_BREAKER_THRESHOLD,
                )
                break
        except Exception as e:
            logger.warning("ZIP %s failed: %s", zip_code, e)

        if max_listings and len(all_listings) >= max_listings:
            logger.info("--max-listings %d reached — stopping ZIP scan early", max_listings)
            all_listings = all_listings[:max_listings]
            break

    # Dedup by ZPID — ZIP boundary overlap can add the same property from 2 ZIPs
    seen_zpids: set[str] = set()
    deduped: list[ListingRecord] = []
    for rec in all_listings:
        key = rec.zpid or rec.address.lower()
        if key not in seen_zpids:
            seen_zpids.add(key)
            deduped.append(rec)
    all_listings = deduped

    total_scanned = len(all_listings)
    logger.info("Total raw listings: %d", total_scanned)

    # ── Stage 1: Fast screen ──────────────────────────────────────
    candidates = [l for l in all_listings if stage1_screen(l)]
    logger.info("Stage 1 passed: %d", len(candidates))

    _tag_portfolio_liquidations(candidates)

    # ── Stage 2: Full scoring + change detection ──────────────────
    watchlist_updates: dict = {
        "price_drops": [],
        "dom_milestones": [],
        "back_on_market": [],
    }
    scored: list[ListingRecord] = []

    for listing in candidates:
        changes = _detect_changes(listing, state)
        listing = score_listing(listing, all_listings, arv_cache, api_key, check_flood)

        for change in changes:
            if change == "price_drop":
                prior_price = state.get("watchlist", {}).get(listing.zpid, {}).get("last_price", 0)
                watchlist_updates["price_drops"].append({
                    "address": f"{listing.address}, {listing.city}",
                    "old": prior_price,
                    "new": listing.list_price,
                })
            elif "_crossed" in change:
                watchlist_updates["dom_milestones"].append({
                    "address": f"{listing.address}, {listing.city}",
                    "dom": listing.dom,
                })
            elif change == "back_on_market":
                watchlist_updates["back_on_market"].append(f"{listing.address}, {listing.city}")

        _update_watchlist_entry(listing, state)
        scored.append(listing)

    _tag_double_distressed(scored, known_addresses)

    # ── Qualify ───────────────────────────────────────────────────
    qualifying: list[ListingRecord] = []

    if mode in ("below-market", "both"):
        bm = [
            l for l in scored
            if l.trigger_score >= _min_score and _has_price_signal(l)
        ]
        qualifying.extend(bm)
        logger.info("Below-market: %d", len(bm))

    if mode in ("stale", "both"):
        already = set(id(l) for l in qualifying)
        stale = [l for l in scored if l.dom >= _min_dom and id(l) not in already]
        qualifying.extend(stale)
        logger.info("Stale (DOM ≥ %d): %d", _min_dom, len(stale))

    qualifying.sort(key=lambda l: l.trigger_score, reverse=True)

    # ── Dedup vs already-uploaded ─────────────────────────────────
    to_upload = [
        l for l in qualifying
        if l.address.strip().lower() not in known_addresses
    ]
    dedup_skipped = len(qualifying) - len(to_upload)
    if dedup_skipped:
        logger.info("Dedup: skipped %d already-uploaded", dedup_skipped)

    # ── watch_mode: only emit delta records ───────────────────────
    if watch_mode:
        prev_zpids = set(state.get("watchlist", {}).keys())
        to_upload = [l for l in to_upload if l.zpid not in prev_zpids]
        logger.info("Watch mode: %d new records to emit", len(to_upload))

    # ── Convert to NoticeData ─────────────────────────────────────
    def _mode_for(l: ListingRecord) -> str:
        if l.dom >= _min_dom and not _has_price_signal(l):
            return "stale"
        return "below-market"

    notices = [listing_to_notice_data(l, _mode_for(l)) for l in to_upload]

    # ── Persist state ─────────────────────────────────────────────
    state["arv_cache"] = arv_cache
    state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_listing_state(state)

    elapsed_min = (datetime.now() - start_time).total_seconds() / 60

    run_stats = {
        "total_scanned": total_scanned,
        "stage1_passed": len(candidates),
        "qualifying": len(qualifying),
        "dedup_skipped": dedup_skipped,
        "to_upload": len(to_upload),
        "watchlist_updates": watchlist_updates,
        "newly_qualified": to_upload,
        "elapsed_min": elapsed_min,
    }

    # ── Slack summary ─────────────────────────────────────────────
    if notify_slack and config.SLACK_WEBHOOK_URL:
        try:
            from slack_notifier import _send_webhook
            summary = build_listing_slack_summary(
                to_upload,
                watchlist_updates,
                total_scanned=total_scanned,
                stage1_passed=len(candidates),
                elapsed_min=elapsed_min,
            )
            _send_webhook(summary)
            logger.info("Slack listing summary sent")
        except Exception as e:
            logger.warning("Slack notification failed: %s", e)

    return notices, run_stats
