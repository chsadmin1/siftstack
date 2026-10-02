"""One-shot: enrich + skip trace for an already-uploaded DataSift list."""
import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

LIST_NAME = "SiftStack 2026-06-17"


async def main():
    import config
    from playwright.async_api import async_playwright
    from datasift_core import login, dismiss_popups as _dismiss_popups, screenshot as _screenshot
    from datasift_uploader import enrich_records, skip_trace_records

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        try:
            ok = await login(page, config.DATASIFT_EMAIL, config.DATASIFT_PASSWORD)
            if not ok:
                logging.error("Login failed")
                return

            logging.info("=== Enrich ===")
            enrich_result = await enrich_records(page, LIST_NAME)
            logging.info("Enrich: %s", enrich_result)

            await page.wait_for_timeout(3000)

            logging.info("=== Skip Trace ===")
            skip_result = await skip_trace_records(page, LIST_NAME)
            logging.info("Skip trace: %s", skip_result)

            logging.info("Done. Keeping browser open 15s for inspection...")
            await page.wait_for_timeout(15000)
        finally:
            await browser.close()


asyncio.run(main())
