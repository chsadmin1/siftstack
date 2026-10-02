"""One-off comp package builder for 13905 E 267th St, Harrisonville, MO 64701.

Missouri is a non-disclosure state (confirmed live against the OpenWeb Ninja
Zillow /search API on this pull: every RECENTLY_SOLD record returned blank
soldPrice/unformattedPrice/price fields for the whole 64701 zip, while
FOR_SALE list prices were fully populated). Rather than triangulate sold
prices comp-by-comp (workable but heavy: 1 property-details-address call per
comp), this build uses CURRENT ACTIVE LISTING PRICES directly as the comp
set - list price is public even in a non-disclosure state, only the closed
price is hidden. Simpler, and it sidesteps the non-disclosure problem
entirely instead of working around it.

This is a genuinely rural 20-acre subject, so "boundary" here is an acreage
floor (>=1 acre, non-LOT) inside a ~13mi radius of the subject, not a
subdivision street list.

Trade-off, stated once and carried through the workbook: an active list
price is an ASK, not a confirmed sale. Two comps in this set (26401 E 267th
and 22701 Timberview) show clear stale-listing / price-cut signals, which is
exactly why the report leans on the bedroom-band clamp and flags every
number as ceiling-ish rather than confirmed.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import sys
sys.path.insert(0, str(Path(__file__).parent))
from rehab_estimator import estimate_rehab

NAVY, BLUE, GREEN, GOLD = "0A1130", "316AFF", "1B9E5A", "B8860B"
SOFT_COST_PCT = 0.13
GUT_ALLOWANCE_PER_SQFT = 15.0

SUBJECT = {
    "address": "13905 E 267th St, Harrisonville, MO 64701",
    "county": "Cass County, MO",
    "parcel_id": "142210000000006000",
    "beds": 2,
    "baths": 0.75,
    "sqft": 1852,
    "year_built": 1940,
    "acres": 20.0,
    "zestimate": 427900,
    "rent_zestimate": 1633,
    "tax_assessed_value": 36800,
    "tax_paid": 2332.67,
    "status": "Not for sale / off-market (no MLS listing history at all)",
    "lat": 38.63232,
    "lon": -94.43685,
}

# Active listings, >=1 acre, non-LOT, within ~13mi of the subject (pulled from
# the OpenWeb Ninja /search FOR_SALE endpoint for the 64701 zip, then
# distance-filtered). List price used directly - no ESP/triangulation.
ACTIVE_COMPS = [
    dict(address="26401 E 267th St", beds=4, baths=3, sqft=1986, acres=10.0,
         year_built=2021, price=635900, dist_mi=7.77,
         note="Same street as subject. Listed 2025-04-07 @ $639,900, pulled unsold "
              "2025-05-12. Relisted 2026-06-10 @ $635,900, still active. ~15 months "
              "combined market time without a buyer - a stale/ceiling-test listing, "
              "not a market-clearing price.",
         url="https://www.zillow.com/homedetails/26401-E-267th-St-Harrisonville-MO-64701/211351974_zpid/"),
    dict(address="22701 Timberview Rd", beds=2, baths=1, sqft=1496, acres=9.9,
         year_built=1880, price=395000, dist_mi=9.45,
         note="ONLY other 2-bed rural comp in the set. Listed 2026-07-10 @ $425,000, "
              "cut -7% to $395,000 on 08-21, still no pending as of this pull "
              "(~9 weeks on market). Likely clears somewhat under current ask.",
         url="https://www.zillow.com/homedetails/22701-Timberview-Rd-Harrisonville-MO-64701/97069317_zpid/"),
    dict(address="23820 S State Highway EE", beds=3, baths=2, sqft=1806, acres=10.0,
         year_built=1972, price=400000, dist_mi=8.52,
         note="Brand new to market (day 0), 'sold as is' language - the one comp in "
              "this set explicitly flagged as unrenovated/distressed.",
         url="https://www.zillow.com/homedetails/23820-S-State-Highway-Ee-Hwy-Harrisonville-MO-64701/464514373_zpid/"),
    dict(address="14608 E 263rd St", beds=5, baths=4, sqft=4400, acres=10.0,
         year_built=None, price=1250000, dist_mi=0.85,
         note="Closest comp by distance (0.85mi) but far larger/higher-end - luxury "
              "outlier, kept for context only.",
         url="https://www.zillow.com/homedetails/14608-E-263rd-St-Harrisonville-MO-64701/97040832_zpid/"),
    dict(address="27611 S Belle Plain Rd", beds=3, baths=3, sqft=2650, acres=12.0,
         year_built=None, price=799000, dist_mi=3.97,
         note="", url="https://www.zillow.com/homedetails/27611-S-Belle-Plain-Rd-Harrisonville-MO-64701/97040420_zpid/"),
    dict(address="900 Holly Ave", beds=4, baths=3, sqft=2406, acres=1.45,
         year_built=None, price=409000, dist_mi=5.60,
         note="Only 1.45 acres - least rural comp in the set.",
         url="https://www.zillow.com/homedetails/900-Holly-Ave-Harrisonville-MO-64701/97038202_zpid/"),
    dict(address="27817 S State Route 7 S", beds=4, baths=2, sqft=2067, acres=1.15,
         year_built=None, price=425000, dist_mi=6.39,
         note="Only 1.15 acres - least rural comp in the set.",
         url="https://www.zillow.com/homedetails/27817-S-State-Route-7-S-Harrisonville-MO-64701/97040493_zpid/"),
    dict(address="26009 S Summit Rd", beds=4, baths=3, sqft=3408, acres=3.0,
         year_built=None, price=1100000, dist_mi=6.79,
         note="", url="https://www.zillow.com/homedetails/26009-S-Summit-Rd-Harrisonville-MO-64701/245584179_zpid/"),
    dict(address="26001 S Summit Rd", beds=4, baths=4, sqft=4802, acres=3.0,
         year_built=None, price=1100000, dist_mi=6.80,
         note="", url="https://www.zillow.com/homedetails/26001-S-Summit-Rd-Harrisonville-MO-64701/97037437_zpid/"),
    dict(address="19301 E 215th St", beds=4, baths=4, sqft=2611, acres=3.0,
         year_built=None, price=650000, dist_mi=7.51,
         note="", url="https://www.zillow.com/homedetails/19301-E-215th-St-Harrisonville-MO-64701/97068388_zpid/"),
    dict(address="20107 E 215th St", beds=3, baths=2, sqft=2184, acres=3.12,
         year_built=None, price=309900, dist_mi=7.76,
         note="", url="https://www.zillow.com/homedetails/20107-E-215th-St-Harrisonville-MO-64701/97068321_zpid/"),
    dict(address="34301 E State Route 2", beds=4, baths=3, sqft=3638, acres=10.56,
         year_built=None, price=665000, dist_mi=12.75,
         note="", url="https://www.zillow.com/homedetails/34301-E-State-Route-2-Harrisonville-MO-64701/97036544_zpid/"),
]

LOT_COMPS = [
    ("29610 S West Outer Rd", 5.2, 95000),
    ("E 323rd St", 49.91, 450000),
]


def land_value_estimate(subject_acres: float) -> dict:
    """Power-law interpolation between the only 2 priced bare-land comps in the
    pulled radius (both LOT listings, asking price). Rural land shows steep
    per-acre economies of scale (a 5.2ac tract asks 2x/acre what a 49.91ac
    tract does), so a straight average would overstate value for a 20ac
    parcel - log-log (power-law) fit on the 2 anchors instead."""
    (a1, ac1, p1), (a2, ac2, p2) = LOT_COMPS
    ppa1, ppa2 = p1 / ac1, p2 / ac2
    b = (math.log(ppa1) - math.log(ppa2)) / (math.log(ac1) - math.log(ac2))
    ln_a = math.log(ppa1) - b * math.log(ac1)
    ppa_subject = math.exp(ln_a) * subject_acres ** b
    return {
        "ppa_subject": round(ppa_subject),
        "land_value": round(ppa_subject * subject_acres / 1000) * 1000,
        "anchor_low": (a2, ac2, round(ppa2)),
        "anchor_high": (a1, ac1, round(ppa1)),
    }


def active_list_arv():
    """Bedroom-band ARV off current asking prices only (no sold-price
    triangulation). Same-bed (2bd) set is thin (n=1: Timberview) -> widen to
    +/-1 bed, excluding the one explicitly distressed/as-is comp so the
    'base' band reflects normal-condition asking prices; clamp to the
    same-bed ceiling since a 2-bed rural buyer pool caps regardless of the
    subject's extra sqft."""
    for c in ACTIVE_COMPS:
        c["ppsf"] = round(c["price"] / c["sqft"], 1) if c["sqft"] else None

    same_bed = [c for c in ACTIVE_COMPS if c["beds"] == SUBJECT["beds"]]
    normal_condition = [c for c in ACTIVE_COMPS if "as is" not in c["note"].lower()]
    widened = [c for c in normal_condition if abs(c["beds"] - SUBJECT["beds"]) <= 1]

    widened_ppsf = statistics.median(c["ppsf"] for c in widened)
    raw = widened_ppsf * SUBJECT["sqft"]
    thin_flag = len(same_bed) < 3
    discounted = raw * 0.90 if thin_flag else raw

    same_bed_ceiling = same_bed[0]["price"] if len(same_bed) == 1 else (
        statistics.median(c["price"] for c in same_bed) if same_bed else None)
    list_price_estimate = min(discounted, same_bed_ceiling) if same_bed_ceiling else discounted

    # Ask-to-sale haircut: two of the widened comps show clear stale/price-cut
    # signals (26401 sat ~15mo unsold before this pull, though it is 4bd and
    # already excluded from `widened`; Timberview itself cut -7% with no
    # pending after 9 weeks). A flat 5% haircut converts "what sellers are
    # asking" to "what a buyer should expect to pay," consistent with the
    # non-disclosure framework's Method A default for a 7-30 day-old ask.
    expected_sale_estimate = list_price_estimate * 0.95

    as_is_comp = next((c for c in ACTIVE_COMPS if "as is" in c["note"].lower()), None)

    return {
        "widened_n": len(widened),
        "widened_ppsf": round(widened_ppsf, 1),
        "raw": round(raw),
        "thin_flag": thin_flag,
        "discounted": round(discounted),
        "same_bed_n": len(same_bed),
        "same_bed_ceiling": same_bed_ceiling,
        "list_price_estimate": round(list_price_estimate / 1000) * 1000,
        "expected_sale_estimate": round(expected_sale_estimate / 1000) * 1000,
        "as_is_comp": as_is_comp,
        "widened_comps": widened,
    }


def rehab_scenarios():
    def total(est, drop=(), extra=0.0):
        rooms = [r for r in est.rooms if r.category not in drop]
        subtotal = sum(r.total for r in rooms) + extra
        return round(subtotal * (1 + SOFT_COST_PCT))

    sqft, beds, baths, yr = SUBJECT["sqft"], SUBJECT["beds"], SUBJECT["baths"], SUBJECT["year_built"]
    cosmetic = estimate_rehab("", sqft, beds, baths, yr, tier=2, scope="wholetail", region="national")
    full = estimate_rehab("", sqft, max(beds, 3), max(baths, 2.0), yr, tier=2, scope="full", region="national")
    gut_extra = GUT_ALLOWANCE_PER_SQFT * sqft
    return {
        "cosmetic": total(cosmetic),
        "mid": total(full, drop=("Roof", "Windows", "Foundation/Structural")),
        "full_gut": total(full, extra=gut_extra),
    }


def _header(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.fill = PatternFill("solid", fgColor=NAVY)


def _title(ws, text, sub=""):
    ws.cell(row=1, column=1, value=text).font = Font(bold=True, size=14, color=NAVY)
    if sub:
        ws.cell(row=2, column=1, value=sub).font = Font(size=10, color="666666")


def _widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def build(out_path: str):
    arv = active_list_arv()
    rehab = rehab_scenarios()
    land = land_value_estimate(SUBJECT["acres"])
    value_estimate = arv["expected_sale_estimate"]
    as_is_comp = arv["as_is_comp"]

    wb = Workbook()

    # ── Summary ──────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Summary"
    _title(ws, f"{SUBJECT['address']} - Comp Package",
           f"Built {datetime.now():%m/%d/%Y}. Value estimate derived from CURRENT ACTIVE "
           "LISTING PRICES only (Missouri hides closed sale prices; asking price is public "
           "either way). Rural 20-acre subject: comps filtered to >=1 acre, non-LOT, within "
           "~13mi.")

    mao70 = {k: round(value_estimate * 0.70 - v) for k, v in rehab.items()}
    mao75 = {k: round(value_estimate * 0.75 - v) for k, v in rehab.items()}

    rows = [
        ("SUBJECT", ""),
        ("Specs", f"{SUBJECT['sqft']:,} sqft | {SUBJECT['beds']}bd/{SUBJECT['baths']}ba | "
                  f"built {SUBJECT['year_built']} | {SUBJECT['acres']:.0f} acres"),
        ("County / Parcel", f"{SUBJECT['county']} | Parcel {SUBJECT['parcel_id']}"),
        ("Status", SUBJECT["status"]),
        ("Zillow Zestimate (headline)", f"${SUBJECT['zestimate']:,} - NOT independently "
                                          "verified against a sale; treat as a data point only"),
        ("Rent Zestimate", f"${SUBJECT['rent_zestimate']:,}/mo"),
        ("Tax assessed value / tax paid", f"${SUBJECT['tax_assessed_value']:,} / "
                                           f"${SUBJECT['tax_paid']:,.0f}/yr"),
        ("", ""),
        ("THE NUMBER", ""),
        ("VALUE ESTIMATE (active-comp based)", f"${value_estimate:,.0f}"),
        ("  basis", f"{arv['widened_n']} active comps (2-3bd, normal condition) median "
                     f"${arv['widened_ppsf']}/sf x {SUBJECT['sqft']:,} sqft = "
                     f"${arv['raw']:,.0f}"
                     + (f", -10% thin-comp discount = ${arv['discounted']:,.0f}" if arv["thin_flag"] else "")
                     + f", clamped to the same-bed asking ceiling (${arv['same_bed_ceiling']:,.0f}) "
                       f"= ${arv['list_price_estimate']:,.0f} list-price estimate, "
                       "-5% ask-to-sale haircut (see ARV + Deal Math sheet)."),
        ("Flag", "Every input here is an ASKING price, not a confirmed sale. Same-bed (2bd) "
                 "set is a single comp (Timberview) that has already taken one price cut with "
                 "no pending offer - treat this number as a ceiling, re-anchor the moment a "
                 "2-bed rural comp in this corridor actually goes under contract."),
        ("As-is reference point", (f"${as_is_comp['price']:,} - {as_is_comp['address']}, the "
          "one comp explicitly listed 'as is,' day 0 on market (no time-on-market feedback "
          "yet). A single, unconfirmed data point - not a bucket.") if as_is_comp else "n/a"),
        ("Full gut rehab", f"${rehab['full_gut']:,.0f} (${rehab['full_gut']/SUBJECT['sqft']:.0f}/sf) "
                            "- DEFAULT assumption: zero MLS history, 1940 build, 'Other' heat / "
                            "no central cooling on record"),
        ("Mid reno rehab", f"${rehab['mid']:,.0f} (${rehab['mid']/SUBJECT['sqft']:.0f}/sf)"),
        ("Cosmetic rehab", f"${rehab['cosmetic']:,.0f} (${rehab['cosmetic']/SUBJECT['sqft']:.0f}/sf)"),
        ("", ""),
        ("REHAB SANITY CHECK",
         f"Full gut (${rehab['full_gut']:,.0f}) is {rehab['full_gut']/value_estimate*100:.0f}% "
         f"of the ${value_estimate:,.0f} value estimate. The bedroom-band clamp caps the "
         "renovated case at what the only other 2-bed rural comp is asking, so a big rehab "
         "spend here is unlikely to move the sale price past that ceiling - a full flip does "
         "not look like the play at this bed count in this corridor. The as-is reference point "
         "above (a 3-bed comp, not bedroom-clamped) is not directly comparable to this "
         "2-bed-clamped estimate, so no before/after delta is shown."),
        ("", ""),
        ("LAND VALUE CROSS-CHECK (20 acres)",
         f"${land['land_value']:,.0f} (${land['ppa_subject']:,}/acre, power-law fit off 2 "
         f"priced bare-land comps: {land['anchor_high'][0]} at {land['anchor_high'][1]}ac / "
         f"${land['anchor_high'][2]:,}/ac, and {land['anchor_low'][0]} at "
         f"{land['anchor_low'][1]}ac / ${land['anchor_low'][2]:,}/ac). Land alone is "
         f"~{land['land_value']/value_estimate*100:.0f}% of the value estimate above - the "
         f"1940 house itself likely contributes only ~${value_estimate - land['land_value']:,.0f} "
         "of that. A buyer here is largely paying for the acreage."),
        ("", ""),
        ("WHOLESALE MATH (off the value estimate, full gut assumption)", ""),
        ("MAO 70% - full gut", f"${mao70['full_gut']:,.0f}"),
        ("MAO 75% - full gut", f"${mao75['full_gut']:,.0f}"),
        ("MAO 70% - mid reno", f"${mao70['mid']:,.0f}"),
        ("MAO 70% - cosmetic (if bones are actually good)", f"${mao70['cosmetic']:,.0f}"),
        ("", ""),
        ("RENTAL / LANDLORD FALLBACK", ""),
        ("1% rule check", f"Rent Zestimate ${SUBJECT['rent_zestimate']:,}/mo needs an all-in "
                           f"price under ${SUBJECT['rent_zestimate']*100:,} to clear 1%. The "
                           f"value estimate (${value_estimate:,.0f}) is already "
                           f"{value_estimate/(SUBJECT['rent_zestimate']*100):.1f}x that ceiling. "
                           "LANDLORD/BRRRR DOES NOT PENCIL here - rural acreage carries land "
                           "value that rent does not monetize."),
        ("", ""),
        ("RURAL-SPECIFIC FLAGS (not in the standard $/sf tiers)", ""),
        ("Systems", "resoFacts show heating 'Other' and no central cooling on record - likely "
                     "propane/wood/window-unit setup on an 85-year-old house. Central HVAC "
                     "install is a real cost the rehab tiers above do not itemize separately."),
        ("Well/septic", "20-acre rural parcel almost certainly runs private well + septic, not "
                          "municipal water/sewer. Budget an inspection; a failed septic system "
                          "is a $10K-$25K surprise the $/sf rehab tiers do not capture."),
        ("Land use / split potential", "Zoning and any acreage-split feasibility were not "
                                         "checked (Cass County zoning/plat records, out of "
                                         "scope for this API-only pull) - verify before pricing "
                                         "a land-value exit."),
    ]
    for i, r in enumerate(rows):
        ws.cell(row=4 + i, column=1, value=r[0])
        ws.cell(row=4 + i, column=2, value=r[1])
        if r[0] and not r[1]:
            ws.cell(row=4 + i, column=1).font = Font(bold=True, color=BLUE)
        if r[0].startswith("  "):
            ws.cell(row=4 + i, column=1).font = Font(italic=True, size=9, color="666666")
    _widths(ws, [34, 115])

    # ── Active Comps ──────────────────────────────────────────────
    ws = wb.create_sheet("Active Comps")
    _title(ws, "Active listings, ~13mi radius, >=1 acre, non-LOT",
           "List price used directly - no sold-price triangulation. Sorted by distance from "
           "subject. Same-bed (2bd) and the as-is comp are highlighted.")
    hdr = ["Address", "Bd", "Ba", "SqFt", "Acres", "Built", "List Price", "$/SF", "Dist (mi)", "Note", "URL"]
    for j, h in enumerate(hdr):
        ws.cell(row=4, column=j + 1, value=h)
    _header(ws, 4, len(hdr))
    for i, c in enumerate(sorted(ACTIVE_COMPS, key=lambda x: x["dist_mi"])):
        vals = [c["address"], c["beds"], c["baths"], c["sqft"], c["acres"], c["year_built"],
                c["price"], c["ppsf"], c["dist_mi"], c["note"], c["url"]]
        for j, v in enumerate(vals):
            ws.cell(row=5 + i, column=j + 1, value=v)
        ws.cell(row=5 + i, column=7).number_format = "$#,##0"
        ws.cell(row=5 + i, column=8).number_format = "$#,##0"
        if c["beds"] == SUBJECT["beds"]:
            ws.cell(row=5 + i, column=1).font = Font(bold=True, color=GREEN)
        elif "as is" in c["note"].lower():
            ws.cell(row=5 + i, column=1).font = Font(bold=True, color=GOLD)
    _widths(ws, [26, 4, 4, 7, 7, 7, 11, 8, 9, 55, 45])

    # ── Rehab Scenarios ──────────────────────────────────────────────
    ws = wb.create_sheet("Rehab Scenarios")
    _title(ws, f"Rehab scenarios - {SUBJECT['sqft']:,} sqft, built {SUBJECT['year_built']}",
           "SiftStack rehab engine, NATIONAL multiplier (no KC-metro calibration in the "
           "codebase - verify against a local contractor), tier 2, incl. 13% soft costs. "
           "Full gut adds $15/sf demo-drywall allowance. Excludes well/septic and any central "
           "HVAC install - see Summary flags.")
    for i, (label, cost) in enumerate([
        ("Cosmetic (wholetail, keeps bed count, assumes good bones)", rehab["cosmetic"]),
        ("Mid reno (kitchen/baths/systems, no envelope)", rehab["mid"]),
        ("Full gut (everything + demo allowance) - DEFAULT for this subject", rehab["full_gut"]),
    ]):
        ws.cell(row=4 + i, column=1, value=label)
        c = ws.cell(row=4 + i, column=2, value=cost)
        c.number_format = "$#,##0"
        ws.cell(row=4 + i, column=3, value=f"${cost / SUBJECT['sqft']:,.0f}/sf")
    _widths(ws, [58, 14, 10])

    # ── ARV + Deal Math ──────────────────────────────────────────────
    ws = wb.create_sheet("ARV + Deal Math")
    _title(ws, "Active-comp value logic (bedroom-band rule)")
    same_bed_list = ", ".join(c["address"] for c in ACTIVE_COMPS if c["beds"] == SUBJECT["beds"])
    widened_list = ", ".join(f"{c['address']} ${c['ppsf']}/sf" for c in arv["widened_comps"])
    lines = [
        ("Subject", f"{SUBJECT['beds']}bd / {SUBJECT['sqft']:,} sqft / {SUBJECT['acres']:.0f} "
                     "acres / built 1940 / no MLS history"),
        ("", ""),
        ("Step 1 - Same-bed (2bd) set", f"{same_bed_list} (n={arv['same_bed_n']}) - THIN, "
                                          "asking price only, already cut once with no pending. "
                                          "Below the n>=3 threshold."),
        ("Step 2 - Widen +/-1 bed, drop the as-is comp",
         f"n={arv['widened_n']}: {widened_list}"),
        ("Step 3 - Median $/sf", f"${arv['widened_ppsf']}/sf"),
        ("Step 4 - Raw estimate", f"${arv['widened_ppsf']}/sf x {SUBJECT['sqft']:,} sqft = "
                                    f"${arv['raw']:,.0f}"),
        ("Step 5 - Thin-comp discount (-10%)",
         f"${arv['discounted']:,.0f}" if arv["thin_flag"] else "not applied (n>=3)"),
        ("Step 6 - Clamp to same-bed ceiling",
         f"min(${arv['discounted']:,.0f}, ${arv['same_bed_ceiling']:,.0f}) = "
         f"${arv['list_price_estimate']:,.0f}"),
        ("Step 7 - Ask-to-sale haircut (-5%)", f"${arv['list_price_estimate']:,.0f} x 0.95 = "
                                                 f"${value_estimate:,.0f}"),
        ("VALUE ESTIMATE", f"${value_estimate:,.0f}"),
        ("", ""),
        ("Why a 5% haircut", "Two of the closest comps show real stale-listing signal: 26401 "
                              "E 267th sat ~15 months combined without a buyer, and Timberview "
                              "itself (the same-bed anchor) cut its own price -7% and still has "
                              "no pending offer after 9 weeks. A flat 5% ask-to-sale discount is "
                              "the non-disclosure framework's own default for a comp this fresh "
                              "on market; treat it as a labeled assumption, not a measured fact."),
        ("", ""),
        ("As-is reference", (f"{as_is_comp['address']}: ${as_is_comp['price']:,} asking, day 0, "
          "'sold as is' language. One comp, no market feedback yet - kept as a labeled data "
          "point, not folded into the median above.") if as_is_comp else "n/a"),
        ("", ""),
        ("Ceiling test", "26401 E 267th St (same street, 4bd/3ba, 10ac, new-build, $635,900) "
                          "has sat active/relisted ~15 months combined without a buyer. A "
                          "4-bed new-build cannot clear $320/sf here; a dated 2-bed farmhouse "
                          "clearing anywhere near that number is not realistic."),
        ("", ""),
        ("Land value cross-check", f"${land['land_value']:,.0f} at ${land['ppa_subject']:,}/ac "
                                     "(power-law interpolation, not a straight average, because "
                                     "rural land shows steep per-acre economies of scale)"),
        ("  anchor - small parcel", f"{land['anchor_high'][0]}: {land['anchor_high'][1]} "
                                      f"acres @ ${land['anchor_high'][2]:,}/ac"),
        ("  anchor - large parcel", f"{land['anchor_low'][0]}: {land['anchor_low'][1]} acres "
                                      f"@ ${land['anchor_low'][2]:,}/ac"),
        ("  implied structure value", f"Value estimate (${value_estimate:,.0f}) minus land "
                                        f"(${land['land_value']:,.0f}) = "
                                        f"~${value_estimate - land['land_value']:,.0f} for the "
                                        "house itself"),
    ]
    for i, (a, b) in enumerate(lines):
        ws.cell(row=4 + i, column=1, value=a)
        ws.cell(row=4 + i, column=2, value=b)
        if a and not a.startswith("Step") and not b:
            ws.cell(row=4 + i, column=1).font = Font(bold=True, color=BLUE)
    _widths(ws, [32, 110])

    # ── Buyer Targets ──────────────────────────────────────────────
    ws = wb.create_sheet("Buyer Targets")
    _title(ws, "Buyer targeting notes",
           "No SiftMap/deed-level buyer database coverage for Cass County, MO in this "
           "codebase (buyer_sweep.py and the DataSift buyer-prospecting exports are Knox/"
           "Blount TN only) - this sheet is directional, not a ranked deed-verified list.")
    notes = [
        ("Land / lifestyle buyers", "Surrounding listing language skews hobby-farm / rural "
         "lifestyle ('tree farm and nursery', 'hobby farm', 'in home business') rather than "
         "flip-and-resell - the dominant buyer pool here likely wants acreage + character over "
         "pristine finishes, and may buy the shell directly without a full flip."),
        ("Land-only demand", "Multiple LOT-only sales/actives in the pulled radius ($95K-$450K "
         "for 5-50 acre bare parcels) confirm active land-value demand independent of the "
         "house - worth a teardown-vs-rehab comparison if the structure checks out badly."),
        ("Flip/rental investors", "Smaller pool than a suburban zip - rural rehabs need a "
         "contractor willing to drive out, and the 1% rule fails badly on rent (see Summary). "
         "Pitch as a cash-flow-negative buy-and-hold ONLY to a land-banking buyer, not a "
         "yield-driven landlord."),
        ("Next step to get real names", "A Cass County Register of Deeds pull for cash/entity "
         "buyers on the comps in this report (mirrors buyer_sweep.py's method, just needs a "
         "non-TN data source) would convert this from directional guidance to a ranked list."),
    ]
    for i, (a, b) in enumerate(notes):
        ws.cell(row=4 + i, column=1, value=a).font = Font(bold=True, color=BLUE)
        ws.cell(row=5 + i, column=1, value=b)
    _widths(ws, [110])

    # ── Methodology ──────────────────────────────────────────────────
    ws = wb.create_sheet("Methodology")
    _title(ws, "Active-listing-price methodology", "Missouri non-disclosure workaround")
    meth = [
        "Missouri does not require public disclosure of closed sale prices. Verified live on "
        "this pull: querying the OpenWeb Ninja Zillow /search endpoint for "
        "home_status=RECENTLY_SOLD across the whole 64701 zip returned 41 rows with soldPrice, "
        "unformattedPrice AND price all blank/None on every single row, while the identical "
        "call with home_status=FOR_SALE returned full list prices on all 41 rows.",
        "",
        "Rather than triangulate an Estimated Sold Price per comp (workable, but needs a "
        "property-details-address call per comp - 1 API call each), this build uses CURRENT "
        "ACTIVE LISTING PRICES as the comp set directly. List price is public in a "
        "non-disclosure state; only the closed price is hidden. Simpler, and the resulting "
        "number is explicitly an asking-price-based estimate rather than a derived sale price.",
        "",
        "Trade-off, stated plainly: an ask is not a confirmed sale. Two of the tightest comps "
        "here show real stale-listing signal (26401 E 267th ~15 months combined on market "
        "unsold; 22701 Timberview Rd, the ONLY same-bed comp, already cut -7% with no pending "
        "after 9 weeks). A flat 5% ask-to-sale haircut is applied to the final estimate, "
        "labeled and easy to strip out - see ARV + Deal Math for the exact math trail.",
        "",
        "Condition/bucketing: full listing descriptions were not pulled for every comp in this "
        "set (that would cost a property-details-address call per comp again), so RENOVATED "
        "vs DISTRESSED bucketing was NOT attempted here beyond the one comp explicitly marked "
        "'sold as is.' Every other comp is treated as normal-condition asking inventory. If a "
        "tighter renovated/distressed split matters for this deal, pulling descriptions for "
        "the same-bed and adjacent-bed comps would be the next step (see the prior, "
        "sold-price-triangulated version of this report for that fuller methodology).",
        "",
        "Confidence: WIDE band, per standard non-disclosure-market guidance. The same-bed "
        "(2-bedroom) comp set is a single active, price-cut, not-yet-pending listing - this "
        "value estimate is a ceiling, not a confirmed number, and should be re-anchored the "
        "moment Timberview Rd (or any other 2-bed rural comp in this corridor) actually goes "
        "under contract or a fresh 2-bed comp appears.",
    ]
    for i, line in enumerate(meth):
        ws.cell(row=3 + i, column=1, value=line)
        ws.cell(row=3 + i, column=1).alignment = Alignment(wrap_text=True)
    ws.column_dimensions["A"].width = 130

    wb.save(out_path)
    return out_path, arv, rehab, land


if __name__ == "__main__":
    out = str(Path(r"C:\Users\djpkc\SiftStack\output") / "13905_E_267th_St_Comp_Package.xlsx")
    path, arv, rehab, land = build(out)
    print(f"Value estimate: ${arv['expected_sale_estimate']:,.0f}")
    print(f"Rehab: cosmetic ${rehab['cosmetic']:,.0f} | mid ${rehab['mid']:,.0f} | "
          f"gut ${rehab['full_gut']:,.0f}")
    print(f"Land value cross-check: ${land['land_value']:,.0f}")
    print(f"Workbook: {path}")
