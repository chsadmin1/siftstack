"""Long-running scheduler for P1 List Manager -- the container's CMD.

Modeled directly on src/ftm_schedule.py (same sleep-loop-in-one-process shape,
same reasons: a specific business-local hour beats Fly's coarse machine
schedules, and one process keeps this job's state on one volume). The one
real difference: ftm_schedule.py's FTM_SCHEDULE_DAYS is weekday-only, but this
job needs a weekday slot (Saturday) AND a day-of-month slot (the 28th) in the
same loop, so `Slot` below generalizes to N named kinds instead of the two
fixed "run"/"health" kinds ftm_schedule.py has.

    python src/p1_manager/schedule.py                 # run forever
    python src/p1_manager/schedule.py --once weekly    # run the weekly job now, then exit
    python src/p1_manager/schedule.py --once monthly   # run the monthly job now, then exit
    python src/p1_manager/schedule.py --next           # print the next 5 fire times

Configuration (all env vars, business-local time, never UTC -- a naive
schedule drifts by an hour twice a year and fires at the wrong time):
  P1_TIMEZONE            default "America/New_York"
  P1_WEEKLY_AT           HH:MM (default "08:00")
  P1_WEEKLY_DAY          weekday name (default "saturday")
  P1_MONTHLY_AT          HH:MM (default "08:00")
  P1_MONTHLY_DAY         day of month, 1-28 (default "28" -- Dan's own call,
                         and deliberately <=28 so it exists in every month)
  P1_WEEKLY_DRY_RUN      "1" to pass commit=False to weekly.run() (default "0")
  P1_MONTHLY_DRY_RUN     "1" to pass commit=False to monthly.run() (default "0")
  P1_RUN_ON_BOOT         "1" to fire whichever job is soonest once at startup
  P1_JITTER_SECONDS      random 0..N delay before firing (default 0)

Requires SLACK_BOT_TOKEN / SLACK_SIGNING_SECRET / SLACK_CHANNEL_ID (see
slack_app.py) for the weekly job's ask step. If any is missing, run_weekly()
falls back to scanning + tag-backfilling only, with a loud warning -- it
never silently skips the ask without saying so.
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import monthly  # noqa: E402
import weekly  # noqa: E402

logger = logging.getLogger("p1_schedule")

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _tz() -> ZoneInfo:
    name = os.getenv("P1_TIMEZONE", "America/New_York")
    try:
        return ZoneInfo(name)
    except Exception:
        logger.warning("Unknown P1_TIMEZONE %r; falling back to UTC", name)
        return ZoneInfo("UTC")


def _parse_hhmm(raw: str, label: str) -> tuple[int, int]:
    try:
        h, m = raw.strip().split(":")
        hh, mm = int(h), int(m)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
    except ValueError:
        pass
    logger.error("Bad %s=%r (want HH:MM); defaulting to 08:00", label, raw)
    return 8, 0


def _weekday_index(raw: str) -> int:
    name = raw.strip().lower()
    for i, wd in enumerate(_WEEKDAYS):
        if wd.startswith(name):
            return i
    logger.error("Unknown P1_WEEKLY_DAY=%r; defaulting to saturday", raw)
    return 5


def _month_day() -> int:
    raw = os.getenv("P1_MONTHLY_DAY", "28").strip()
    try:
        d = int(raw)
        if 1 <= d <= 28:
            return d
    except ValueError:
        pass
    logger.error("Bad P1_MONTHLY_DAY=%r (want 1-28); defaulting to 28", raw)
    return 28


@dataclass(frozen=True)
class Slot:
    name: str
    hh: int
    mm: int
    weekday: int | None = None     # 0=Monday .. 6=Sunday, mutually exclusive with day_of_month
    day_of_month: int | None = None

    def next_after(self, now: datetime) -> datetime:
        for day_offset in range(0, 35):  # covers a whole month of lookahead
            day = (now + timedelta(days=day_offset)).date()
            if self.weekday is not None and day.weekday() != self.weekday:
                continue
            if self.day_of_month is not None and day.day != self.day_of_month:
                continue
            candidate = datetime(day.year, day.month, day.day, self.hh, self.mm,
                                 tzinfo=now.tzinfo)
            if candidate > now:
                return candidate
        raise RuntimeError(f"no fire time found for slot {self.name!r} within 35 days")


def slots() -> list[Slot]:
    wh, wm = _parse_hhmm(os.getenv("P1_WEEKLY_AT", "08:00"), "P1_WEEKLY_AT")
    mh, mm = _parse_hhmm(os.getenv("P1_MONTHLY_AT", "08:00"), "P1_MONTHLY_AT")
    return [
        Slot("weekly", wh, wm, weekday=_weekday_index(os.getenv("P1_WEEKLY_DAY", "saturday"))),
        Slot("monthly", mh, mm, day_of_month=_month_day()),
    ]


def _slack_ready() -> bool:
    return all(os.environ.get(k) for k in
               ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CHANNEL_ID"))


def run_weekly() -> None:
    """Never raises: the scheduler must survive one bad run and fire the
    next slot on schedule regardless."""
    dry = os.getenv("P1_WEEKLY_DRY_RUN", "0").strip() in ("1", "true", "yes", "on")
    logger.info("Firing weekly job (commit=%s)", not dry)
    post_slack = None
    if not dry and _slack_ready():
        import slack_app
        post_slack = slack_app.post_weekly_ask
    elif not dry:
        logger.warning("SLACK_BOT_TOKEN/SLACK_SIGNING_SECRET/SLACK_CHANNEL_ID not "
                        "all set -- this run will scan and tag-backfill new P1 "
                        "records but will NOT post/ask about SiftLine board+phase.")
    started = time.time()
    try:
        result = weekly.run(commit=not dry, post_slack=post_slack)
        logger.info("Weekly job finished in %.0fs: %s",
                    time.time() - started, result)
    except Exception:
        logger.exception("Weekly job raised, scheduler continuing")


def run_monthly() -> None:
    dry = os.getenv("P1_MONTHLY_DRY_RUN", "0").strip() in ("1", "true", "yes", "on")
    logger.info("Firing monthly job (commit=%s)", not dry)
    started = time.time()
    try:
        result = monthly.run(commit=not dry, pull_date=date.today())
        logger.info("Monthly job finished in %.0fs: %d P1 presets, %d records repulled",
                    time.time() - started, result.get("total_p1_presets", 0),
                    result.get("repull", {}).get("total_records", 0))
        if not dry and _slack_ready():
            import slack_app
            slack_app.post_monthly_report(result["report_text"],
                                          result.get("repull", {}).get("csv_path"))
        elif not dry:
            logger.warning("SLACK_* not all set -- monthly report was NOT posted to Slack.")
    except Exception:
        logger.exception("Monthly job raised, scheduler continuing")


RUNNERS = {"weekly": run_weekly, "monthly": run_monthly}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="P1 List Manager scheduler")
    p.add_argument("--once", choices=["weekly", "monthly"],
                   help="Run one job immediately, then exit")
    p.add_argument("--next", action="store_true", help="Print the next 5 fire times and exit")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    tz = _tz()
    the_slots = slots()
    logger.info("Scheduler up. tz=%s slots=%s",
                os.getenv("P1_TIMEZONE", "America/New_York"),
                ", ".join(f"{s.name}={s.hh:02d}:{s.mm:02d}"
                         f"{' ' + _WEEKDAYS[s.weekday][:3] if s.weekday is not None else ''}"
                         f"{' day' + str(s.day_of_month) if s.day_of_month is not None else ''}"
                         for s in the_slots))

    if args.next:
        now = datetime.now(tz)
        events = []
        for s in the_slots:
            when = now
            for _ in range(5):
                when = s.next_after(when)
                events.append((when, s.name))
        for when, name in sorted(events)[:10]:
            print(f"  {when.isoformat(timespec='minutes')}  ({when.tzname()})  {name}")
        return 0

    if args.once:
        RUNNERS[args.once]()
        return 0

    if os.getenv("P1_RUN_ON_BOOT", "").strip() in ("1", "true", "yes", "on"):
        logger.info("P1_RUN_ON_BOOT set, firing the soonest job immediately")
        now = datetime.now(tz)
        soonest = min(the_slots, key=lambda s: s.next_after(now))
        RUNNERS[soonest.name]()

    jitter_max = int(os.getenv("P1_JITTER_SECONDS", "0") or 0)

    while True:
        now = datetime.now(tz)
        target, target_slot = min(
            ((s.next_after(now), s) for s in the_slots), key=lambda pair: pair[0])

        wait = (target - datetime.now(tz)).total_seconds()
        logger.info("Next %s at %s (in %.1f h)", target_slot.name,
                    target.isoformat(timespec="minutes"), wait / 3600)
        # Sleep in chunks so a restart or DST shift is picked up promptly.
        while wait > 0:
            time.sleep(min(wait, 300))
            wait = (target - datetime.now(tz)).total_seconds()

        if jitter_max > 0:
            jitter = random.uniform(0, jitter_max)
            logger.info("Jitter: waiting a further %.0fs", jitter)
            time.sleep(jitter)

        RUNNERS[target_slot.name]()

        # Guard against firing twice inside the same minute.
        time.sleep(61)


if __name__ == "__main__":
    raise SystemExit(main())
