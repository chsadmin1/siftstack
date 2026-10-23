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

import json
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

        state = context.storage_state()
        browser.close()

    # Keep only smrtPhone's own cookies and localStorage. Everything that reads
    # this file looks for those alone, and the trimmed file is small enough to
    # store as a GitHub secret for the CHS SMS agent (see below).
    state = {
        "cookies": [c for c in state.get("cookies", []) if "smrt" in c.get("domain", "")],
        "origins": [o for o in state.get("origins", []) if "smrt" in o.get("origin", "")],
    }
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    print(f"Session saved -> {STATE_PATH} ({len(state['cookies'])} smrtPhone cookies)")
    print()
    print("CHS SMS agent: copy it into the SMRTPHONE_SESSION GitHub secret with")
    print(f'  [Convert]::ToBase64String([IO.File]::ReadAllBytes("{STATE_PATH}")) | Set-Clipboard')
    print("then paste into GitHub > Settings > Secrets and variables > Actions >")
    print("SMRTPHONE_SESSION, and run Actions > 'Upload smrtPhone session'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
