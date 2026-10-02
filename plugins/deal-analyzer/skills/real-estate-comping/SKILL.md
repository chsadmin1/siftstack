---
name: real-estate-comping
description: Perform AI-powered property valuation and comparable sales analysis for real estate wholesaling. Use when the user needs to comp a property, determine ARV, analyze comparable sales, or perform property valuation. Automatically detects disclosure vs non-disclosure states and applies the appropriate methodology (standard comping for disclosure states, triangulation method for non-disclosure states like Texas). This skill focuses purely on comping — determining market value through comparable sales analysis. It does NOT estimate rehab costs or renovation budgets; those are handled separately.
---

# Real Estate Comping Skill

Perform appraiser-grade property valuations using the Two-Bucket method for disclosure states or the Triangulation method for non-disclosure states. This skill is strictly about comparable sales analysis and ARV determination — it does not estimate rehab costs, renovation budgets, or scope of work. If the user needs rehab cost estimation, direct them to the appropriate skill for that.

## Workflow Overview

1. **Identify property location** → Determine state from address
2. **Route to correct methodology** → Disclosure or Non-Disclosure framework
3. **Execute 9-step analysis** → Follow the appropriate prompt framework
4. **Generate deliverables** → PDF summary report + Excel breakdown + comps table

## Comp Data Acquisition (before any analysis)

**Primary path: the Zillow /search API pull.** If an OpenWeb Ninja Real-Time Zillow Data key is available (`OPENWEBNINJA_API_KEY` env var), pull the comp universe programmatically instead of browsing listing sites. The companion **comp-package** skill ships the full contract and a self-contained puller script. The load-bearing facts:
- `/search` with `home_status=RECENTLY_SOLD` (exact enum; other casings return 400) for solds, `FOR_SALE` for actives
- Every search caps at 41 rows (about 5 weeks of sales in an active zip): partition by `min_price`/`max_price` bands and recursively split any band returning 41 (recovers 12-24 months per zip)
- `price_min`/`price_max` are silently ignored; confirm every filter against the echoed `parameters` object
- `dateSold` is epoch milliseconds; use `unformattedPrice`, not the `soldPrice` display string
- Clip to the target pocket with a lat/lon bounding box AND a street-name whitelist; verify LOT-type or missing-sqft surprises against the county card (often teardown sales or new builds)
- The API is MLS-only: auction, wholesale, and off-market transfers come from county records

**Fallback path:** no API key, or the user prefers manual comping: browse Zillow/Redfin/MLS exactly as the framework references describe. The methodology below is identical either way; only data acquisition changes.

**Bedroom-band rule (both paths):** when the subject's bedroom count is below the comp set (especially 2-bed vs 3-bed), run a dual-track ARV: the base case is valued ONLY against same-bed comps, clamped to that band's median price (extra sqft does not escape the band); reconfig to more bedrooms is a labeled upside credited only after a walkthrough verifies the layout converts. Underwrite off the base track, and project future value on the same-bed curve.

## State Detection & Routing

**Determine state type from property address:**

- **Non-Disclosure States** (sold prices not publicly recorded): TX, UT, WY, NM, ID, MT, ND, AK, KS, MS, LA, MO
- **Disclosure States**: All other US states

**Routing:**
- Non-disclosure state → Read `references/non-disclosure-prompt.md`
- Disclosure state → Read `references/disclosure-prompt.md`

## Quick Reference

| Framework | States | Key Method | Price Source |
|-----------|--------|------------|--------------|
| Disclosure | Most US states | Two-Bucket (Unrenovated vs Renovated PPSF) | MLS sold prices |
| Non-Disclosure | TX, UT, WY, NM, ID, MT, ND, AK, KS, MS, LA, MO | Triangulation (LLP + DOM, Deed of Trust, Tax Ratio) | Derived estimates |

## Core Comping Rules (Both Frameworks)

The full rule set — GLA definition, comp selection filters, outlier detection protocol, market sentiment table, renovation/bucket classification, and every feature-adjustment table (bedroom/bath/garage/pool/view/lot/foundation/etc.) — lives in **`references/adjustment-cheatsheet.md`** and is walked step-by-step in the routed framework file (`disclosure-prompt.md` or `non-disclosure-prompt.md`, Steps 1-9 including a worked Two-Bucket example). Read the routed framework file before analyzing — it is the authoritative, complete procedure. Do not re-derive these rules from memory; the cheatsheet and framework files are the source of truth.

**One-line summary of the core method:** the Two-Bucket approach splits comps into unrenovated (Bucket A) vs renovated (Bucket B) to derive a market-observed renovation premium (PPSF_B vs PPSF_A) — a market signal, not a rehab cost estimate. Normal spread is 5-30%; outside that range, re-examine both buckets before trusting the number.

## Required Deliverables

**Every comp analysis MUST produce these two outputs:**

### 1. Excel Breakdown Workbook
Comprehensive multi-sheet workbook with:
- **Executive Summary** sheet: Quick-view of key findings
- **Subject Property** sheet: All property details
- **Comparable Sales** sheet: Full comps table with bucket analysis
- **Adjustments Detail** sheet: Line-by-line adjustment breakdown
- **Market Analysis** sheet: Market metrics and trends
- **ARV Calculation** sheet: Step-by-step ARV math
- **Sources & Notes** sheet: Data sources, parameters, recommendations

**Generate using:** `scripts/generate_excel_report.py`

### 2. In-Context Analysis
The detailed analysis text with tables shown directly in the conversation, including:
- Step-by-Step ARV Breakdown (Base PPSF → Adjustments → Final ARV)
- Comps Summary Table (Address, Sale Date, Price, GLA, Beds/Baths, Year, Condition, Adjustments, Final Adjusted Value)
- Outlier Screening Results (any comps flagged or excluded, with reasons)
- Market Overview (Median price, PPSF, DOM, sale-to-list ratio, market phase, sentiment assessment)
- Sources & Assumptions (Data sources, time window, radius constraints)
- Recommendations & Caveats (Verification steps, risk factors, disclaimer)

## Output Generation Instructions

### Data Structure for Report Generation

Prepare analysis data as JSON with this structure:

```json
{
    "subject_property": {
        "address": "123 Main St",
        "city": "Austin",
        "state": "TX",
        "zip": "78701",
        "county": "Travis",
        "subdivision": "Downtown",
        "property_type": "Single Family",
        "gla": 1850,
        "lot_size": 6500,
        "beds": 3,
        "baths": 2,
        "year_built": 1985,
        "condition": "Dated"
    },
    "comps": [
        {
            "address": "456 Oak Ave",
            "sale_date": "2025-12-15",
            "sale_price": 485000,
            "gla": 1780,
            "ppsf": 272.47,
            "beds": 3,
            "baths": 2,
            "year_built": 1982,
            "condition": "Renovated",
            "distance": 0.3,
            "total_adjustments": -5000,
            "adjusted_value": 480000,
            "outlier_flag": false,
            "outlier_reason": null
        }
    ],
    "outlier_screening": {
        "median_ppsf": 255.00,
        "std_dev_ppsf": 18.50,
        "threshold_low": 218.00,
        "threshold_high": 292.00,
        "excluded_comps": [],
        "flagged_comps": []
    },
    "bucket_analysis": {
        "unrenovated": { "count": 2, "median_ppsf": 235.20, "avg_ppsf": 235.20 },
        "renovated": { "count": 2, "median_ppsf": 257.33, "avg_ppsf": 257.33 },
        "market_premium_pct": 9.4,
        "spread_sanity": "normal"
    },
    "market_sentiment": {
        "dom_trend": "stable",
        "inventory_months": 4.2,
        "yoy_price_change_pct": 2.1,
        "sale_to_list_ratio": 0.98,
        "classification": "balanced",
        "sentiment_adjustment_pct": 3.5
    },
    "market_overview": {
        "market_phase": "Balanced",
        "median_price": 455000,
        "median_ppsf": 248.50,
        "avg_dom": 28,
        "sale_to_list_ratio": 0.98,
        "active_count": 45,
        "pending_count": 22,
        "notes": ["Market observations..."]
    },
    "arv_calculation": {
        "base_ppsf": 235.20,
        "market_premium_pct": 9.4,
        "renovated_ppsf": 257.33,
        "subject_gla": 1850,
        "base_arv": 476061,
        "feature_adjustments": -5000,
        "sentiment_adjustment_pct": 3.5,
        "final_arv": 471000,
        "confidence_level": "Moderate",
        "confidence_band_pct": 5.0,
        "arv_low": 447450,
        "arv_high": 494550
    },
    "adjustments_applied": [
        {
            "comp_number": 1,
            "comp_address": "456 Oak Ave",
            "adjustment_type": "GLA",
            "reason": "Subject 70 sqft larger",
            "amount": -5000
        }
    ],
    "sources": ["MLS", "County Records", "Zillow"],
    "search_parameters": {
        "time_window": "90 days",
        "radius": "0.5 miles",
        "gla_range": "1600-2100 sqft"
    },
    "recommendations": ["Verification steps..."],
    "caveats": ["Disclaimers..."]
}
```

### Generate Reports

1. Save the analysis data to a JSON file
2. Run the Excel generator:
   ```bash
   python scripts/generate_excel_report.py output_report.xlsx data.json
   ```

## Execution Instructions

1. Gather the property address (and any known context: condition, seller notes, known issues) from the user.
2. Determine disclosure status and load the matching framework file (see State Detection & Routing above) — follow its 9 steps exactly; that file owns outlier screening, sentiment, and bucket classification.
3. Optional: for block-by-block markets, use the Zillow boundary tool to draw a tighter comp area.
4. Compile the completed analysis into the JSON structure above and run `generate_excel_report.py`.
5. Deliver both outputs to the user: the Excel workbook and the in-context analysis (per Required Deliverables above).

## Special Considerations

### Non-Disclosure State Caveats
- Wider confidence bands (+/-5-7% vs +/-2-5%)
- Must derive sold prices using triangulation methods
- Recommend "Option Period Verification" once under contract

### Source Code Alignment (comp_analyzer.py)

The automated comp analyzer uses Knoxville, TN-calibrated adjustment values (<$500K tier). See the "Source Code Reference" table at the top of `references/adjustment-cheatsheet.md` for the exact figures, and the same file's per-feature tables for the scaled >$500K values.
