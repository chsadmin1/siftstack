"""One-shot: skip trace the Cass County SiftMap Priority Pull list."""
import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

LIST_NAME = "SiftMap Priority Pull - Cass MO 2026-07"
STATE_PATH = r"C:\Users\djpkc\AppData\Local\Temp\claude\c--Users-djpkc-SiftStack\acbd28ce-e1c5-4b41-b312-f021914f6e6a\scratchpad\datasift_state.json"
SCRATCH = r"C:\Users\djpkc\AppData\Local\Temp\claude\c--Users-djpkc-SiftStack\acbd28ce-e1c5-4b41-b312-f021914f6e6a\scratchpad"


async def main():
    from playwright.async_api import async_playwright
    from datasift_uploader import _filter_by_list, _dismiss_popups, _screenshot

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(storage_state=STATE_PATH, viewport={"width": 1600, "height": 1100})
        page = await context.new_page()

        await page.goto("https://app.reisift.io/records/properties", wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
        await _dismiss_popups(page)
        await page.evaluate("document.querySelectorAll('#intercom-container').forEach(el => el.remove());")

        filtered = await _filter_by_list(page, LIST_NAME)
        logging.info("Filtered: %s", filtered)
        await page.wait_for_timeout(1500)
        await page.evaluate("document.querySelectorAll('#intercom-container').forEach(el => el.remove());")

        # Open the header "Choose selection" menu and pick "Select all (N)"
        header_label = page.locator('label.Checkbox__StyledLabel-llvwVi').first
        if await header_label.count() == 0:
            header_label = page.locator('[class*="CheckboxDropdownInnerContainer"] label').first
        await header_label.click(force=True)
        await page.wait_for_timeout(1200)

        select_all_opt = page.get_by_text("Select all (", exact=False).first
        opt_text = await select_all_opt.inner_text()
        logging.info("Selecting: %s", opt_text)
        await select_all_opt.click(force=True)
        await page.wait_for_timeout(1500)
        await _screenshot(page, "cass_selected_all")

        await _dismiss_popups(page)
        await page.evaluate("document.querySelectorAll('#intercom-container').forEach(el => el.remove());")
        await page.wait_for_timeout(300)

        send_to_btn = page.locator('button:has-text("Send To")')
        if await send_to_btn.count() == 0:
            send_to_btn = page.locator('button:has-text("Send to")')
        await send_to_btn.first.click(force=True)
        await page.wait_for_timeout(1200)

        skip_option = page.locator('text="Skip Trace"')
        if await skip_option.count() == 0:
            skip_option = page.locator('text="Skip trace"')
        await skip_option.first.click(force=True)
        await page.wait_for_timeout(2000)
        await _screenshot(page, "cass_skiptrace_modal")

        agree_btn = page.locator('button:has-text("I Agree with the terms")')
        if await agree_btn.count() == 0:
            agree_btn = page.locator('button:has-text("I Agree")')
        if await agree_btn.count() > 0:
            await agree_btn.first.click(force=True)
            logging.info("Clicked 'I Agree with the terms'")
            await page.wait_for_timeout(2000)
        else:
            logging.error("No 'I Agree' button found")
            await _screenshot(page, "cass_no_agree")
            await browser.close()
            return

        await _screenshot(page, "cass_review_step")

        tag_input = page.locator('input[placeholder*="tag"], input[placeholder*="Tag"], input[placeholder*="Add tag"]')
        if await tag_input.count() > 0:
            tag = "SiftMap Priority Pull - Cass MO"
            await tag_input.first.fill(tag)
            await page.wait_for_timeout(500)
            await tag_input.first.press("Enter")
            await page.wait_for_timeout(500)
            await tag_input.first.fill("")
            await page.wait_for_timeout(300)
            await page.evaluate("""() => {
                document.querySelectorAll('[class*="InputSuggestion"]').forEach(el => el.remove());
            }""")
            logging.info("Added skip trace tag: %s", tag)

        await _screenshot(page, "cass_ready_to_confirm")

        confirmed = False
        for btn_text in ["Skip Trace", "Skip Trace Records", "Start Skip Trace", "Submit", "Confirm", "Process"]:
            skip_btn = page.locator(f'button:has-text("{btn_text}")')
            if await skip_btn.count() > 0:
                await skip_btn.first.click(force=True)
                logging.info("Clicked '%s' — processing started", btn_text)
                await page.wait_for_timeout(3000)
                confirmed = True
                break
        if not confirmed:
            logging.error("Could not find skip trace submit button")

        await _screenshot(page, "cass_skiptrace_submitted")
        await page.wait_for_timeout(5000)
        await context.storage_state(path=STATE_PATH)
        await browser.close()

        logging.info("Done. confirmed=%s", confirmed)


asyncio.run(main())
