"""
Logs into numberverifier.com once, then visits every configured
NUMBERVERIFIER_REPORT_N_URL (up to 8) and captures a screenshot of that
number's Caller ID Detail section (the AT&T / T-Mobile / Verizon caller-ID
mockups) plus a small metadata block (number, label, home carrier).

Does NOT classify spam/clean itself -- that is a visual judgment call made
by whoever (or whatever) reads the screenshots afterward. This script's only
job is to reliably get fresh, dated screenshots + metadata out of the site.

Usage:
    python src/number_verifier_capture.py

Output:
    output/number_verifier/<YYYY-MM-DD>/report_<n>_<number>.png
    output/number_verifier/<YYYY-MM-DD>/manifest.json

Exit codes:
    0  all configured reports captured successfully
    1  missing credentials / no report URLs configured
    2  login did not succeed (no reports could be captured -- this is a
       real failure, not an empty-but-successful run)
    3  one or more (but not all) reports failed to capture
"""
import json
import os
import re
import sys
from datetime import date

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

EMAIL = os.environ.get("NUMBERVERIFIER_EMAIL")
PASSWORD = os.environ.get("NUMBERVERIFIER_PASSWORD")
LOGIN_URL = "https://app.numberverifier.com/login"
OUT_ROOT = "output/number_verifier"


def get_report_urls():
    urls = []
    for i in range(1, 9):
        url = os.environ.get(f"NUMBERVERIFIER_REPORT_{i}_URL", "").strip()
        if url:
            urls.append((i, url))
    return urls


def login(page):
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    page.locator('input[type="email"]').first.fill(EMAIL)
    page.locator('input[type="password"]').first.fill(PASSWORD)
    page.locator('button[type="submit"]').first.click()
    page.wait_for_timeout(2500)


def login_succeeded(page, report_urls):
    # Prove the session actually authenticated by loading the first report
    # and checking for content only a logged-in session can see.
    if not report_urls:
        return False
    _, first_url = report_urls[0]
    page.goto(first_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)
    body_text = page.inner_text("body")
    return "Caller ID Detail" in body_text


def parse_metadata(body_text):
    meta = {}
    m = re.search(r"Caller ID Detail - (\S+)\s*\(([^)]+)\)", body_text)
    if m:
        meta["number"] = m.group(1)
        meta["label"] = m.group(2)
    m = re.search(r"Carrier:\s*(\S+)", body_text)
    if m:
        meta["home_carrier"] = m.group(1)
    m = re.search(r"Monthly Plan:\s*([\d/]+ Numbers)", body_text)
    if m:
        meta["plan"] = m.group(1)
    return meta


def capture_report(page, url, out_path):
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2500)
    body_text = page.inner_text("body")
    meta = parse_metadata(body_text)

    # Clip the screenshot to end just above "Remediation History" so it's
    # just the calendar + carrier caller-ID mockups, not the long history table.
    clip = None
    try:
        marker = page.locator("text=Remediation History").first
        if marker.count() > 0:
            box = marker.bounding_box()
            if box:
                clip = {"x": 0, "y": 0, "width": 1400, "height": max(int(box["y"]) - 10, 400)}
    except Exception:
        clip = None

    if clip:
        page.screenshot(path=out_path, clip=clip)
    else:
        page.screenshot(path=out_path, full_page=True)

    return meta


def main():
    if not EMAIL or not PASSWORD:
        print("FATAL: missing NUMBERVERIFIER_EMAIL / NUMBERVERIFIER_PASSWORD in .env")
        sys.exit(1)

    report_urls = get_report_urls()
    if not report_urls:
        print("FATAL: no NUMBERVERIFIER_REPORT_N_URL values configured in .env")
        sys.exit(1)

    today = date.today().isoformat()
    out_dir = os.path.join(OUT_ROOT, today)
    os.makedirs(out_dir, exist_ok=True)

    manifest = {"date": today, "reports": []}
    failures = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 1200})
        page = context.new_page()

        login(page)

        if not login_succeeded(page, report_urls):
            print("FATAL: login did not succeed -- no authenticated content visible on report page")
            browser.close()
            sys.exit(2)

        for i, url in report_urls:
            out_path = os.path.join(out_dir, f"report_{i}.png")
            try:
                meta = capture_report(page, url, out_path)
                if not meta.get("number"):
                    raise RuntimeError("could not parse 'Caller ID Detail - <number> (<label>)' heading")
                meta["report_index"] = i
                meta["url"] = url
                meta["screenshot"] = out_path
                manifest["reports"].append(meta)
                print(f"OK  report {i}: {meta.get('number')} ({meta.get('label')}) -> {out_path}")
            except Exception as exc:
                failures.append({"report_index": i, "url": url, "error": str(exc)})
                print(f"FAIL report {i} ({url}): {exc}")

        browser.close()

    manifest["failures"] = failures
    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nManifest written to {manifest_path}")
    print(f"Captured {len(manifest['reports'])}/{len(report_urls)} reports.")

    if not manifest["reports"]:
        sys.exit(2)
    if failures:
        sys.exit(3)
    sys.exit(0)


if __name__ == "__main__":
    main()
