"""Scrapers for Kansas county public notices.

Johnson County KS — Sheriff foreclosure sale listings (jocogov.org).
Wyandotte County KS — Tax sale listings (wycokck.org). [stub — needs investigation]
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta

from playwright.async_api import Page, TimeoutError as PwTimeout, async_playwright

import config
from config import (
    JOHNSON_FORECLOSURE_URL,
    WYANDOTTE_TAX_SALE_URL,
    KsSearch,
    OUTPUT_DIR,
    REQUEST_DELAY_MIN,
    REQUEST_DELAY_MAX,
    STATE_FILE,
)
from notice_parser import NoticeData

logger = logging.getLogger(__name__)

_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


async def _delay() -> None:
    import random
    await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))


async def _debug_screenshot(page: Page, name: str) -> None:
    try:
        path = OUTPUT_DIR / f"debug_{name}.png"
        await page.screenshot(path=str(path))
        logger.info("Debug screenshot: %s", path)
    except Exception:
        pass


def _parse_sale_date(text: str) -> str:
    """Try to parse a sale date string into YYYY-MM-DD."""
    # "July 15, 2025" or "7/15/2025"
    m = re.search(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if m:
        mon = _MONTH_MAP.get(m.group(1).lower(), "")
        if mon:
            return f"{m.group(3)}-{mon}-{m.group(2).zfill(2)}"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        try:
            return datetime.strptime(m.group(0), "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


# ── Johnson County KS — Sheriff Foreclosure Sales ─────────────────────


async def _scrape_johnson_foreclosures(
    page: Page,
    since_date: str,
    llm_api_key: str | None,
) -> list[NoticeData]:
    """Scrape Johnson County KS upcoming foreclosure sales from jocogov.org."""
    logger.info("Scraping Johnson County KS foreclosures from %s", JOHNSON_FORECLOSURE_URL)
    notices: list[NoticeData] = []

    try:
        await page.goto(JOHNSON_FORECLOSURE_URL, wait_until="domcontentloaded", timeout=30_000)
        # JS-rendered Drupal page — wait for content to settle
        await page.wait_for_load_state("networkidle", timeout=20_000)
        await _delay()

        full_text = await page.evaluate("document.body.innerText")
        logger.debug("Johnson Co page text (first 500): %s", full_text[:500])

        # Try to find structured listing rows — Drupal "views" output
        rows = await page.evaluate("""() => {
            const items = [];

            // Drupal views rows
            const rowEls = document.querySelectorAll('.views-row, .view-content > div, tr');
            rowEls.forEach(row => {
                const text = row.innerText.trim();
                if (text.length > 20) items.push(text);
            });

            if (items.length) return items;

            // Fallback: paragraphs / list items with date-like content
            document.querySelectorAll('p, li').forEach(el => {
                const t = el.innerText.trim();
                if (t.match(/20\\d{2}/) && t.length > 20 && t.length < 500) {
                    items.push(t);
                }
            });
            return items;
        }""")

        if not rows:
            logger.warning("No structured rows found on Johnson Co page — saving screenshot")
            await _debug_screenshot(page, "ks_johnson_no_rows")
            # Fallback: log the page text so we can diagnose
            logger.info("Page text snippet: %s", full_text[:1000])
            return []

        logger.info("Found %d rows on Johnson Co foreclosure page", len(rows))

        for row_text in rows:
            if not row_text.strip():
                continue

            # Try to extract sale date
            sale_date = _parse_sale_date(row_text)

            # Skip notices older than since_date
            if sale_date and sale_date < since_date:
                logger.debug("Skipping old sale (date %s < since %s)", sale_date, since_date)
                continue

            # Try to extract a case number
            case_m = re.search(r"(?:Case|No\.?|#)\s*([\w-]+\d[\w-]*)", row_text, re.IGNORECASE)
            case_num = case_m.group(1) if case_m else ""

            # Try to extract property address
            addr_m = re.search(
                r"(\d{1,5}\s+(?:[NSEW]\.?\s+)?(?:[\w'-]+\s+)+?"
                r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|"
                r"Boulevard|Blvd|Way|Circle|Cir|Court|Ct|Place|Pl|"
                r"Highway|Hwy|Parkway|Pkwy)\b\.?)"
                r"(?:,\s*([A-Za-z ]+?))?"
                r"(?:,?\s*(?:Kansas|KS)\s*,?\s*(\d{5}))?",
                row_text, re.IGNORECASE,
            )
            address = addr_m.group(1).strip().rstrip(",") if addr_m else ""
            city = addr_m.group(2).strip() if addr_m and addr_m.group(2) else ""
            zip_code = addr_m.group(3).strip() if addr_m and addr_m.group(3) else ""

            # Parcel ID as fallback address (enrichment will resolve it)
            if not address:
                parcel_m = re.search(r"(?:Parcel|PIN|ID)[:\s#]*([\w-]+)", row_text, re.IGNORECASE)
                if parcel_m:
                    address = f"PARCEL:{parcel_m.group(1)}"

            # Defendant name = likely the property owner
            owner = ""
            def_m = re.search(
                r"(?:Defendant|Owner|Mortgagor)[:\s]+([A-Z][A-Z\s,.'&/-]{3,60})"
                r"(?:\n|,\s*(?:and\b|Case)|$)",
                row_text,
            )
            if def_m:
                owner = def_m.group(1).strip()

            notice = NoticeData(
                notice_type="foreclosure",
                county="Johnson",
                state="KS",
                address=address,
                city=city,
                zip=zip_code,
                owner_name=owner,
                auction_date=sale_date,
                source_url=JOHNSON_FORECLOSURE_URL,
                raw_text=row_text,
                date_added=datetime.now().strftime("%Y-%m-%d"),
            )

            # Note: if address is a parcel ID, store in parcel_id field too
            if address.startswith("PARCEL:"):
                notice.parcel_id = address[7:]
                notice.address = ""

            notices.append(notice)
            logger.debug("  JoCo foreclosure: sale=%s addr=%s owner=%s",
                         sale_date, address or notice.parcel_id or "?", owner or "?")

    except Exception:
        logger.exception("Johnson County KS scrape failed")

    logger.info("Johnson Co KS: %d foreclosure listings", len(notices))
    return notices


# ── Wyandotte County KS — Tax Sales (stub) ────────────────────────────


async def _scrape_wyandotte_tax_sales(
    page: Page,
    since_date: str,
    llm_api_key: str | None,
) -> list[NoticeData]:
    """Stub scraper for Wyandotte County KS tax sales.

    wycokck.org returns 403 to automated requests. Two alternative approaches
    for a future implementation:
      1. Download the PDF/HTML tax sale list from the direct file URL at
         http://maps.wycokck.org/gisdata/taxsale/ (currently down).
      2. Use Playwright with a real browser profile / residential proxy to
         bypass the 403 (likely Cloudflare or similar WAF).

    For now this logs the known URL and returns empty so the pipeline
    can still run for other counties.
    """
    logger.info(
        "Wyandotte County KS tax sale scraper not yet implemented. "
        "Check %s manually or provide an alternative source URL.",
        WYANDOTTE_TAX_SALE_URL,
    )

    # Attempt a direct fetch as a courtesy — may work if the 403 was transient
    try:
        await page.goto(WYANDOTTE_TAX_SALE_URL, wait_until="domcontentloaded", timeout=20_000)
        status = await page.evaluate("() => document.readyState")
        url_now = page.url
        logger.info("Wyandotte page loaded: url=%s readyState=%s", url_now, status)

        full_text = await page.evaluate("document.body.innerText")
        if full_text and len(full_text) > 200:
            logger.info("Wyandotte page has content (%d chars) — may be scrapeable", len(full_text))
            logger.info("First 500 chars: %s", full_text[:500])
            await _debug_screenshot(page, "ks_wyandotte_page")
        else:
            logger.warning("Wyandotte page returned little/no content")
    except Exception as e:
        logger.warning("Wyandotte page fetch failed: %s", e)

    return []


# ── Public entry point ─────────────────────────────────────────────────


async def scrape_ks_notices(
    searches: list[KsSearch],
    mode: str = "daily",
    since_date_override: str | None = None,
    llm_api_key: str | None = None,
    headless: bool = True,
) -> list[NoticeData]:
    """Scrape Kansas county public notices.

    Returns list[NoticeData] ready for the enrichment pipeline.
    """
    if not searches:
        return []

    if since_date_override:
        since_date = since_date_override
    elif mode == "daily":
        data = config.load_state(STATE_FILE)
        since_date = data.get("last_run_date") or (
            datetime.now() - timedelta(days=7)
        ).strftime("%Y-%m-%d")
    else:
        since_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    logger.info("KS scrape: %d searches, since %s, headless=%s",
                len(searches), since_date, headless)

    all_notices: list[NoticeData] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        try:
            for search in searches:
                if search.source == "johnson_sheriff":
                    batch = await _scrape_johnson_foreclosures(page, since_date, llm_api_key)
                elif search.source == "wyandotte_tax":
                    batch = await _scrape_wyandotte_tax_sales(page, since_date, llm_api_key)
                else:
                    logger.warning("Unknown KS source: %s", search.source)
                    batch = []

                all_notices.extend(batch)
                logger.info("KS %s/%s: %d notices", search.county, search.notice_type, len(batch))

        finally:
            await context.close()
            await browser.close()

    logger.info("KS scrape complete: %d total notices", len(all_notices))
    return all_notices
