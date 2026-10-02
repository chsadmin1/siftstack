"""smrtphone_login.py - headed browser login to capture a SmrtPhone session.

The dialer has no public API; pull_calls.py authenticates by replaying the
browser session's cookies. This opens a real Chromium window, you log in by
hand (including any 2FA), then come back to this console and press Enter
once you're on the logged-in dashboard - it saves smrtphone_state.json at
the repo root in the exact format pull_calls.py expects.

Re-run this whenever pull_calls.py (or daily_audit.py) exits 2
("session expired").

USAGE (from SiftStack root, venv python):
  python src/call_coaching/smrtphone_login.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pull_calls import BASE  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / "smrtphone_state.json"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(BASE)

        print("A Chromium window has opened.")
        print("Log in to SmrtPhone by hand (email/password, 2FA if prompted).")
        print("Once you're on the logged-in dashboard (call log / home screen),")
        input("come back here and press Enter to capture the session... ")

        context.storage_state(path=str(STATE_PATH))
        browser.close()

    print(f"Session saved -> {STATE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
