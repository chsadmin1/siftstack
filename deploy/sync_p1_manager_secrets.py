"""Push P1 List Manager's secrets from .env into its Fly app.

Same pattern as deploy/sync_ftm_secrets.py: reads .env, shows exactly what it
would send with every value MASKED, and only actually transmits with
--commit. Nothing here ever prints a real secret value, including on failure.

    python deploy/sync_p1_manager_secrets.py                 # masked plan, sends nothing
    python deploy/sync_p1_manager_secrets.py --commit        # push to Fly
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_APP = "siftstack-p1-manager"

# What the scheduler actually needs to boot and authenticate to DataSift.
REQUIRED = [
    ("DATASIFT_EMAIL", "mints the JWT every write goes through"),
    ("DATASIFT_PASSWORD", "mints the JWT every write goes through"),
]

# The app runs and does its scan/tag-backfill work without these -- it just
# logs a loud warning and skips the Slack ask step until they're set (see
# schedule.py's _slack_ready()). Add them once the Slack App checklist is done.
OPTIONAL = [
    ("SLACK_BOT_TOKEN", "posts the weekly ask + monthly report"),
    ("SLACK_SIGNING_SECRET", "verifies inbound Slack events are real"),
    ("SLACK_CHANNEL_ID", "where the weekly ask / monthly report post"),
    ("P1_TRIGGER_SECRET", "gates POST /trigger/{job} for manual in-container test runs"),
]


def load_env(path: Path) -> dict:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def mask(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) < 20:
        return f"set (len {len(value)})"
    return f"{value[:3]}...{value[-2:]} (len {len(value)})"


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync P1 List Manager secrets from .env into Fly")
    ap.add_argument("--app", default=DEFAULT_APP)
    ap.add_argument("--env-file", default=str(REPO_ROOT / ".env"))
    ap.add_argument("--commit", action="store_true", help="Actually send to Fly")
    args = ap.parse_args()

    env = load_env(Path(args.env_file))
    for key, _ in REQUIRED + OPTIONAL:
        if os.environ.get(key):
            env[key] = os.environ[key]

    send: dict[str, str] = {}
    missing_required: list[str] = []

    print(f"\nP1 List Manager secrets -> Fly app '{args.app}'")
    print(f"source: {args.env_file}\n")

    print("required:")
    for key, why in REQUIRED:
        val = env.get(key, "")
        if val:
            send[key] = val
            print(f"  [OK  ] {key:24} {mask(val):28} {why}")
        else:
            missing_required.append(key)
            print(f"  [MISS] {key:24} {'':28} {why}")

    print("\noptional:")
    for key, why in OPTIONAL:
        val = env.get(key, "")
        if val:
            send[key] = val
            print(f"  [OK  ] {key:24} {mask(val):28} {why}")
        else:
            print(f"  [ -  ] {key:24} {'':28} {why}")

    if missing_required:
        print(f"\nBLOCKED: missing required {', '.join(missing_required)}")
        print("Nothing sent.\n")
        return 2

    if not send:
        print("\nNothing to send.\n")
        return 1

    if not args.commit:
        print(f"\nDRY RUN. {len(send)} secrets would be set. Re-run with --commit.\n")
        return 0

    # "flyctl", not "fly" -- the Windows install of the Fly CLI in this
    # environment only exposes the former on PATH (verified live 2026-09-23:
    # "fly" 404s with "not found on PATH" while "flyctl version" works fine).
    cmd = ["flyctl", "secrets", "set", "-a", args.app, "--stage"]
    cmd += [f"{k}={v}" for k, v in send.items()]

    print(f"\nSending {len(send)} secrets (staged; applied on next deploy)...")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        print("flyctl not found on PATH. Install it, or run `fly secrets set` by hand.")
        return 1
    except subprocess.TimeoutExpired:
        print("flyctl timed out.")
        return 1

    def scrub(text: str) -> str:
        for v in send.values():
            if v:
                text = text.replace(v, "***")
        return text

    if proc.returncode != 0:
        print(scrub(proc.stderr or proc.stdout))
        return proc.returncode

    print(scrub(proc.stdout))
    print("Staged. Apply with:  fly deploy --config fly.p1-manager.toml\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
