"""Scraper for mopublicnotices.com — Missouri public notices.

Covers: Jackson, Cass, Clay, Platte counties.
Notice types: Foreclosure, Sheriff Sales, Tax Sale.
No login required. reCAPTCHA v2 required on each notice detail page.

Search approach:
  - Use the keyword text search (txtSearch) with "{county} County {keyword}"
  - Set date range via "Last N days" control after expanding the DATE RANGE panel
  - Click btnGo with force=True (footer element intercepts normal clicks)
  - Extract notice IDs from btnView2 onclick attributes in GridView results
  - Navigate to Details.aspx?ID=X, solve reCAPTCHA, extract full notice text
"""

import asyncio
import logging
import random
import re
from datetime import datetime, timedelta

from playwright.async_api import Page, TimeoutError as PwTimeout, async_playwright

import config
from config import (
    MO_BASE_URL,
    MO_RECAPTCHA_SITEKEY,
    MoSearch,
    OUTPUT_DIR,
    REQUEST_DELAY_MIN,
    REQUEST_DELAY_MAX,
    STATE_FILE,
)
from notice_parser import NoticeData

logger = logging.getLogger(__name__)

MO_SEARCH_PATH = "/Search.aspx"
MO_DETAIL_PATH = "/Details.aspx"

# ── Address / owner / date parsing ────────────────────────────────────────

_ADDR_PHRASES = [
    r"commonly known as[:\s]+",
    r"property address[:\s]+",
    r"located at[:\s]+",
    r"street address[:\s]+",
    r"situate(?:d)? (?:and lying )?in .{0,60}?, commonly known as[:\s]+",
]

_STREET_SUFFIX = (
    r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Way|"
    r"Circle|Cir|Court|Ct|Place|Pl|Highway|Hwy|Parkway|Pkwy|Trail|Trl|"
    r"Terrace|Ter|Loop|Run|Path|Ridge|Crossing|Xing|Bend|Point|Pt|"
    r"Row|Trace|Walk|Knoll|Crest|Spur|Commons|Pass|Glen|View)\b"
)

_MO_ADDR_RE = re.compile(
    r"(?:" + "|".join(_ADDR_PHRASES) + r")"
    r"(\d{1,5}\s+(?:[NSEW]\.?\s+)?(?:[\w'-]+\s+)+?" + _STREET_SUFFIX + r"\.?)"
    r"(?:,\s*([A-Za-z ]+?))?"
    r"(?:,\s*(?:Missouri|MO)\s+(\d{5}))?",
    re.IGNORECASE,
)

_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


def _parse_mo_notice_text(
    raw_text: str,
    county: str,
    notice_type: str,
    source_url: str = "",
    date_added: str = "",
) -> NoticeData:
    """Parse Missouri notice body text into NoticeData."""
    notice = NoticeData(
        notice_type=notice_type,
        county=county,
        state="MO",
        raw_text=raw_text,
        source_url=source_url,
        date_added=date_added,
    )

    # ── Address ──────────────────────────────────────────────────────
    m = _MO_ADDR_RE.search(raw_text)
    if m:
        notice.address = m.group(1).strip().rstrip(",")
        if m.group(2):
            notice.city = m.group(2).strip()
        if m.group(3):
            notice.zip = m.group(3).strip()

    # ── Owner Name ────────────────────────────────────────────────────
    # MO notices use mixed case ("Earnest Hausey"), not ALL CAPS.
    owner_pats = [
        # "executed by [NAME]," — stop at first comma (mixed-case MO format)
        r"executed by\s+([A-Za-z][A-Za-z\s.'&/-]+?)(?=,|\n)",
        # "IN RE: [NAME]" — trustee-sale-in-re style
        r"IN RE:\s+([A-Za-z][A-Za-z\s.'&/,\-]+?)(?:,\s*SIGNING|\n|Trustee)",
        r"Grantor[:\s]+([A-Za-z][A-Za-z\s.'&/-]{3,60})(?:\n|,\s*(?:and\b|whose|address))",
        r"Mortgagor[:\s]+([A-Za-z][A-Za-z\s.'&/-]{3,60})(?:\n|,\s*(?:and\b|whose))",
        r"Trustor[:\s]+([A-Za-z][A-Za-z\s.'&/-]{3,60})(?:\n|,\s*(?:and\b|whose))",
        r"[Oo]wner\s+of\s+[Rr]ecord[:\s]+([A-Za-z][A-Za-z\s.'&/-]{3,60})(?:\n|Parcel|Property|Delinquent)",
        r"[Oo]wner[:\s]+([A-Za-z][A-Za-z\s.'&/-]{3,60})(?:\n|Parcel|Property|Delinquent|\$)",
    ]
    bad_owner_words = {"county", "missouri", "trustee", "bank", "mortgage", "national",
                       "trust", "federal", "savings", "financial", "corporation"}
    for pat in owner_pats:
        m = re.search(pat, raw_text, re.DOTALL | re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().rstrip(",.")
            words_lower = {w.lower() for w in candidate.split()}
            if 3 < len(candidate) < 100 and not (words_lower & bad_owner_words):
                notice.owner_name = candidate
                break

    # ── Auction / Sale Date ───────────────────────────────────────────
    auction_pats = [
        r"(?:sale|auction|sold)(?:\s+will be)?(?:\s+held)?\s+on\s+\w+,\s+(\w+)\s+(\d{1,2}),\s+(\d{4})",
        r"(?:sale date|date of sale|scheduled for)[:\s]+(\w+)\s+(\d{1,2}),\s+(\d{4})",
        r"(?:will on|on the)\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})",
        r"(?:sale|auction)\s+on\s+(\d{1,2}/\d{1,2}/\d{4})",
        r"(\d{1,2}/\d{1,2}/\d{4})\s+at\s+\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)",
    ]
    for pat in auction_pats:
        m = re.search(pat, raw_text, re.IGNORECASE)
        if m:
            g = m.groups()
            if len(g) == 3 and not g[0][0].isdigit():
                mon = _MONTH_MAP.get(g[0].lower(), "")
                if mon:
                    notice.auction_date = f"{g[2]}-{mon}-{g[1].zfill(2)}"
            elif len(g) == 1:
                try:
                    notice.auction_date = datetime.strptime(g[0], "%m/%d/%Y").strftime("%Y-%m-%d")
                except ValueError:
                    pass
            if notice.auction_date:
                break

    return notice


# ── Helpers ────────────────────────────────────────────────────────────


async def _delay() -> None:
    await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))


async def _debug_screenshot(page: Page, name: str) -> None:
    try:
        path = OUTPUT_DIR / f"debug_{name}.png"
        await page.screenshot(path=str(path))
        logger.info("Debug screenshot: %s", path)
    except Exception:
        pass


async def _wait_settle(page: Page, timeout: int = 10_000) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except PwTimeout:
        await asyncio.sleep(1.5)


# ── Search form interaction ────────────────────────────────────────────


async def _run_search(
    page: Page,
    keyword: str,
    days_back: int,
) -> bool:
    """Navigate to Search.aspx and submit keyword + date range.

    Uses 'last N days' date control (simpler than explicit date fields).
    Returns True if results page loaded, False on error.
    """
    search_url = f"{MO_BASE_URL}{MO_SEARCH_PATH}"
    try:
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
        await _wait_settle(page)
    except Exception:
        logger.exception("Failed to load MO search page")
        return False

    # Fill keyword in txtSearch (it's always visible)
    try:
        await page.fill("#ctl00_ContentPlaceHolder1_as1_txtSearch", keyword)
    except Exception:
        logger.exception("Failed to fill txtSearch")
        return False

    # Expand DATE RANGE section and set "last N days"
    await page.evaluate("""() => {
        const div = document.getElementById('ctl00_ContentPlaceHolder1_as1_divDateRange');
        if (div) {
            const header = div.querySelector('label.header');
            if (header) header.click();
        }
    }""")
    await page.wait_for_timeout(300)

    # Fill the "last N days" field (clears the others to avoid override)
    await page.evaluate("""(days) => {
        const d = document.getElementById('ctl00_ContentPlaceHolder1_as1_txtLastNumDays');
        const w = document.getElementById('ctl00_ContentPlaceHolder1_as1_txtLastNumWeeks');
        const m = document.getElementById('ctl00_ContentPlaceHolder1_as1_txtLastNumMonths');
        if (w) w.value = '';
        if (m) m.value = '';
        if (d) d.value = String(days);
        // Also clear explicit from/to so they don't interfere
        const f = document.getElementById('ctl00_ContentPlaceHolder1_as1_txtDateFrom');
        const t = document.getElementById('ctl00_ContentPlaceHolder1_as1_txtDateTo');
        if (f) f.value = '';
        if (t) t.value = '';
    }""", days_back)

    # Scroll btnGo into view and click with force (footer intercepts normal clicks)
    await page.evaluate(
        "document.getElementById('ctl00_ContentPlaceHolder1_as1_btnGo')"
        ".scrollIntoView({behavior: 'instant', block: 'center'})"
    )
    await page.wait_for_timeout(200)

    try:
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
            await page.click("#ctl00_ContentPlaceHolder1_as1_btnGo", force=True)
    except PwTimeout:
        # UpdatePanel — no full navigation, results load in-place
        await _wait_settle(page, timeout=15_000)

    return True


# ── Result extraction ──────────────────────────────────────────────────


def _parse_notice_url(onclick_val: str) -> tuple[str, str] | None:
    """Extract (notice_id, full_detail_url) from btnView2 onclick value.

    onclick = "javascript:location.href='Details.aspx?SID=XXX&ID=NNNNNN';return false;"
    Returns (notice_id, relative_url) or None if not parseable.
    """
    # Extract the full relative URL from location.href='...'
    url_m = re.search(r"location\.href=['\"]([^'\"]+)['\"]", onclick_val)
    if not url_m:
        return None
    rel_url = url_m.group(1)
    # Extract just the ID
    id_m = re.search(r"[?&]ID=(\d+)", rel_url)
    if not id_m:
        return None
    return id_m.group(1), rel_url


async def _extract_page_results(page: Page, since_date: str) -> list[dict]:
    """Extract notice IDs, pub dates, and snippets from the current results page.

    Returns list of dicts: {notice_id, pub_date, snippet, publication}.
    """
    items = await page.evaluate("""() => {
        const results = [];
        // Each outer result row has a light-brown background color
        document.querySelectorAll('tr[style*="background-color"]').forEach(row => {
            // Publication info is in the first <td> inside a nested table
            const pubTd = row.querySelector('td:not([class*="view"]):not([colspan])');
            const pubText = pubTd ? pubTd.innerText.trim() : '';

            // Notice snippet is in the td[colspan="3"]
            const snippetTd = row.querySelector('td[colspan="3"]');
            const snippet = snippetTd ? snippetTd.innerText.trim() : '';

            // btnView2 onclick contains the Details.aspx?ID=... URL
            const btn = row.querySelector('input[id*="btnView2"]');
            const onclick = btn ? btn.getAttribute('onclick') || '' : '';

            results.push({pubText, snippet, onclick});
        });
        return results;
    }""")

    results = []
    for item in items:
        onclick = item.get("onclick", "")
        parsed = _parse_notice_url(onclick)
        if not parsed:
            continue
        notice_id, detail_rel_url = parsed

        # Extract publication date from the pub text (e.g., "Tuesday, June 16, 2026")
        pub_text = item.get("pubText", "")
        pub_date = ""
        dm = re.search(
            r"(\w+day),\s+(\w+)\s+(\d{1,2}),\s+(\d{4})", pub_text, re.IGNORECASE
        )
        if dm:
            mon = _MONTH_MAP.get(dm.group(2).lower(), "")
            if mon:
                pub_date = f"{dm.group(4)}-{mon}-{dm.group(3).zfill(2)}"

        # Skip notices older than since_date
        if pub_date and pub_date < since_date:
            continue

        results.append({
            "notice_id": notice_id,
            "detail_url": detail_rel_url,
            "pub_date": pub_date,
            "snippet": item.get("snippet", ""),
            "publication": pub_text[:80],
        })

    return results


async def _get_next_page_url(page: Page) -> bool:
    """Click the next-page button if one exists. Returns True if navigated."""
    # GridView pagination: look for a link/button with "Next" text or ">" symbol
    next_btn = await page.evaluate("""() => {
        // ASP.NET GridView pagination renders as <a> links in a <tr> at bottom
        const links = Array.from(document.querySelectorAll('a, input[type=submit]'));
        for (const el of links) {
            const txt = el.innerText ? el.innerText.trim() : el.value || '';
            if (txt === '>' || txt.toLowerCase() === 'next' || txt === '>>') {
                return {found: true, tag: el.tagName, text: txt};
            }
        }
        // GridView also uses href="javascript:__doPostBack(...)" for pagination
        for (const a of document.querySelectorAll('a[href*="doPostBack"]')) {
            const txt = a.innerText.trim();
            if (/^>$/.test(txt) || /next/i.test(txt)) {
                a.click();
                return {found: true, clicked: true, text: txt};
            }
        }
        return {found: false};
    }""")

    if not next_btn.get("found"):
        return False

    if not next_btn.get("clicked"):
        # Find and click via Playwright
        try:
            el = await page.query_selector("a:has-text('>'), a:has-text('Next')")
            if el:
                try:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=10_000):
                        await el.click(force=True)
                except PwTimeout:
                    await _wait_settle(page)
        except Exception:
            return False

    await _wait_settle(page)
    return True


# ── Notice detail page ─────────────────────────────────────────────────


async def _extract_pdf_text(page: Page, pdf_href: str, notice_id: str) -> str:
    """Download the full notice PDF and extract its text via pypdfium2.

    Uses the browser's request context so session cookies are included.
    Falls back gracefully if the PDF is image-only or download fails.

    The mopublicnotices.com lnkDownload href is server-generated text PDF
    (not scanned image), so direct text extraction reliably returns the full
    notice body including the property address that lblContentText truncates.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.debug("pypdfium2 not available — cannot extract PDF text")
        return ""

    if not pdf_href.startswith("http"):
        pdf_url = f"{MO_BASE_URL}/{pdf_href.lstrip('/')}"
    else:
        pdf_url = pdf_href

    try:
        response = await page.context.request.get(pdf_url, timeout=20_000)
        if response.status != 200:
            logger.warning("PDF download HTTP %d for notice %s", response.status, notice_id)
            return ""
        pdf_bytes = await response.body()
    except Exception as e:
        logger.warning("PDF download failed for notice %s: %s", notice_id, e)
        return ""

    try:
        doc = pdfium.PdfDocument(pdf_bytes)
        pages_text = []
        for i in range(len(doc)):
            pdf_page = doc[i]
            textpage = pdf_page.get_textpage()
            pages_text.append(textpage.get_text_range())
        full_text = "\n".join(pages_text).strip()
        logger.debug("Notice %s: extracted %d chars from PDF (%d pages)",
                     notice_id, len(full_text), len(doc))
        return full_text
    except Exception as e:
        logger.warning("PDF text extraction failed for notice %s: %s", notice_id, e)
        return ""


async def _get_full_notice_text(
    page: Page,
    notice_id: str,
    captcha_api_key: str | None,
    detail_rel_url: str | None = None,
) -> str:
    """Navigate to Details.aspx, solve CAPTCHA, return full notice text.

    detail_rel_url: the relative URL from the onclick (includes SID parameter
    needed by the server). Falls back to plain ID-only URL if not provided.
    """
    if detail_rel_url:
        detail_url = f"{MO_BASE_URL}/{detail_rel_url.lstrip('/')}"
    else:
        detail_url = f"{MO_BASE_URL}{MO_DETAIL_PATH}?ID={notice_id}"
    try:
        await page.goto(detail_url, wait_until="domcontentloaded", timeout=30_000)
        await _wait_settle(page)
    except Exception:
        logger.exception("Failed to load detail page: %s", detail_url)
        return ""

    # If the server couldn't find the notice context, the page shows an error
    # with empty fields — no point solving CAPTCHA.
    body_preview = await page.evaluate(
        "document.body.innerText.slice(0, 500)"
    )
    if "problem loading" in body_preview.lower():
        logger.warning("Notice %s: server returned 'problem loading' (bad SID?), skipping", notice_id)
        await _debug_screenshot(page, f"mo_problem_{notice_id}")
        return ""

    # Attempt CAPTCHA solve — captcha_solver checks internally if content is
    # already visible (no CAPTCHA) and returns True without spending a solve.
    if captcha_api_key:
        from captcha_solver import solve_captcha_and_view
        solved = await solve_captcha_and_view(page, sitekey=MO_RECAPTCHA_SITEKEY)
        if not solved:
            logger.warning("CAPTCHA solve failed for notice %s", notice_id)
            return ""

    # MO-specific content extraction: target lblContentText directly.
    # The pnlNoticeContent panel appears after CAPTCHA solve and contains:
    #   - lblContentText: up to ~1,000 chars of OCR'd PDF text (truncated)
    #   - lnkDownload: href to the full notice PDF (always present after OCR)
    # Property addresses in MO foreclosure notices appear AFTER the ~1,000 char
    # truncation point, so we must fall back to the full PDF whenever possible.
    content_data = await page.evaluate("""() => {
        const lbl = document.getElementById(
            'ctl00_ContentPlaceHolder1_PublicNoticeDetailsBody1_lblContentText');
        const dl = document.getElementById(
            'ctl00_ContentPlaceHolder1_PublicNoticeDetailsBody1_lnkDownload');
        return {
            text: lbl ? lbl.innerText.trim() : '',
            pdf_href: dl ? (dl.getAttribute('href') || '') : '',
        };
    }""")

    raw_text = content_data.get("text", "")
    pdf_href = content_data.get("pdf_href", "")

    # Prefer full PDF text over the truncated web version. The web text is capped
    # at ~1,000 chars which cuts off before MO foreclosure property addresses.
    # Try PDF when: (a) web text is absent, or (b) web text is short (likely truncated).
    if pdf_href and (not raw_text or len(raw_text) < 1500):
        pdf_text = await _extract_pdf_text(page, pdf_href, notice_id)
        if pdf_text and len(pdf_text) > len(raw_text):
            logger.info("Notice %s: using PDF text (%d chars, was %d from web)",
                        notice_id, len(pdf_text), len(raw_text))
            return pdf_text
        elif not pdf_text and not raw_text:
            logger.debug("Notice %s: both lblContentText and PDF empty", notice_id)

    if not raw_text:
        logger.debug("Notice %s: lblContentText empty, no PDF available", notice_id)

    return raw_text


# ── Single search execution ────────────────────────────────────────────


async def _run_mo_search(
    page: Page,
    search: MoSearch,
    days_back: int,
    since_date: str,
    captcha_api_key: str | None,
    max_notices: int,
    seen_ids: set[str],
) -> list[NoticeData]:
    """Run one MO keyword search and collect all resulting notices."""
    keyword = f"{search.county} County {search.search_keyword}"
    logger.info("MO search: %s (%s / %s, last %d days)",
                keyword, search.county, search.notice_type, days_back)

    ok = await _run_search(page, keyword, days_back)
    if not ok:
        return []

    notices: list[NoticeData] = []
    page_num = 1

    while True:
        logger.info("  Results page %d for %s", page_num, keyword)
        page_items = await _extract_page_results(page, since_date)

        if not page_items:
            if page_num == 1:
                logger.info("  No results for keyword %r", keyword)
                await _debug_screenshot(
                    page, f"mo_no_results_{search.county}_{search.notice_type}"
                )
            break

        logger.info("  %d notices on page %d", len(page_items), page_num)

        for item in page_items:
            notice_id = item["notice_id"]
            if notice_id in seen_ids:
                logger.debug("  Skipping duplicate notice ID %s", notice_id)
                continue
            seen_ids.add(notice_id)

            if max_notices and len(notices) >= max_notices:
                logger.info("  max_notices=%d reached", max_notices)
                return notices

            detail_url = f"{MO_BASE_URL}/{item.get('detail_url', '').lstrip('/')}" if item.get('detail_url') else f"{MO_BASE_URL}{MO_DETAIL_PATH}?ID={notice_id}"
            raw_text = await _get_full_notice_text(page, notice_id, captcha_api_key, item.get("detail_url"))

            if not raw_text:
                # lblContentText empty — fall back to search-results snippet.
                # Snippets contain the first ~300 chars of the notice (enough for owner name).
                raw_text = item.get("snippet", "")
                if raw_text:
                    logger.debug("Notice %s: using search results snippet as text", notice_id)

            if raw_text:
                notice = _parse_mo_notice_text(
                    raw_text=raw_text,
                    county=search.county,
                    notice_type=search.notice_type,
                    source_url=detail_url,
                    date_added=item.get("pub_date", ""),
                )
                # LLM fallback if address or owner missing
                if not notice.address or not notice.owner_name:
                    try:
                        from llm_parser import extract_with_llm
                        llm_result = await extract_with_llm(
                            raw_text, search.notice_type,
                            search.county, config.ANTHROPIC_API_KEY, state="MO",
                        )
                        if llm_result:
                            if not notice.address:
                                notice.address = llm_result.get("address", "")
                            if not notice.city:
                                notice.city = llm_result.get("city", "")
                            if not notice.zip:
                                notice.zip = llm_result.get("zip", "")
                            if not notice.owner_name:
                                notice.owner_name = llm_result.get("owner_name", "")
                            if not notice.auction_date:
                                notice.auction_date = llm_result.get("auction_date", "")
                    except Exception as e:
                        logger.warning("LLM fallback failed: %s", e)

                notices.append(notice)
                logger.debug("  + ID=%s addr=%s owner=%s date=%s",
                             notice_id,
                             notice.address or "(no addr)",
                             notice.owner_name or "(no owner)",
                             notice.auction_date or "(no date)")

            await _delay()

            # Return to results page after visiting detail
            try:
                await page.go_back()
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)
                await _wait_settle(page)
            except Exception:
                # Re-run the search if back navigation fails
                logger.warning("go_back failed — re-running search")
                ok = await _run_search(page, keyword, days_back)
                if not ok:
                    return notices
                # Fast-forward to the right page (skip pages already done)
                for _ in range(page_num - 1):
                    if not await _get_next_page_url(page):
                        return notices
                break

        # Next page
        has_next = await _get_next_page_url(page)
        if not has_next:
            break
        page_num += 1
        await _delay()

    logger.info("  %s/%s: %d notices", search.county, search.notice_type, len(notices))
    return notices


# ── Public entry point ─────────────────────────────────────────────────


async def scrape_mo_notices(
    searches: list[MoSearch],
    mode: str = "daily",
    since_date_override: str | None = None,
    llm_api_key: str | None = None,
    max_notices: int = 0,
    headless: bool = True,
) -> list[NoticeData]:
    """Scrape mopublicnotices.com for the given MO county searches.

    Returns list[NoticeData] ready for the enrichment pipeline.
    """
    if not searches:
        return []

    # Determine date range
    if since_date_override:
        since_date = since_date_override
        since_dt = datetime.strptime(since_date, "%Y-%m-%d")
        days_back = (datetime.now() - since_dt).days + 1
    elif mode == "daily":
        state_data = config.load_state(STATE_FILE)
        last = state_data.get("last_run_date")
        if last:
            since_date = last
            since_dt = datetime.strptime(since_date, "%Y-%m-%d")
            days_back = (datetime.now() - since_dt).days + 1
        else:
            days_back = 7
            since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    else:  # historical
        days_back = 365
        since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    logger.info("MO scrape: %d searches, since %s (%d days), headless=%s",
                len(searches), since_date, days_back, headless)

    captcha_api_key = config.CAPTCHA_API_KEY or None

    all_notices: list[NoticeData] = []
    seen_ids: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 1200},
        )
        page = await context.new_page()

        try:
            for search in searches:
                batch = await _run_mo_search(
                    page=page,
                    search=search,
                    days_back=days_back,
                    since_date=since_date,
                    captcha_api_key=captcha_api_key,
                    max_notices=max_notices,
                    seen_ids=seen_ids,
                )
                all_notices.extend(batch)
        finally:
            await context.close()
            await browser.close()

    logger.info("MO scrape complete: %d total notices from %d searches",
                len(all_notices), len(searches))
    return all_notices
