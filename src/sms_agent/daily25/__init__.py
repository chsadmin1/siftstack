"""Daily 35 for Wendy: Acquisitions + Lead Flow SiftLine boards.

Three scheduled stages (all America/Chicago):

    08:00  select_run.py     pick the top 35, create Wendy's call tasks,
                              Trestle-score any untagged picked phone, draft
                              the pre-call text into the account's existing
                              "LeadFlow Pre-Call Text" field, persist today's
                              batch.
    09:00  send_run.py       queue the pre-call text for each of today's 35
                              (minus cooldown/suppressed/invalid), strictly
                              alternating Wendy's two numbers, ~2min apart.
    18:00  voicemail_run.py  re-check each record's Call Disposition; for
                              anything marked Left Voicemail today, queue the
                              matching post-call follow-up text.

Each stage is runnable by hand:

    python -m sms_agent.daily25.select_run --doctor
    python -m sms_agent.daily25.select_run --dry-run
    python -m sms_agent.daily25.select_run --commit --limit 1   # verify one first
    python -m sms_agent.daily25.select_run --commit

    python -m sms_agent.daily25.send_run --commit --limit 1
    python -m sms_agent.daily25.voicemail_run --commit --limit 1

In production, sms_agent/worker.py's `_start_daily25_threads()` fires each
stage automatically once DAILY25_ENABLED=1 -- off by default until --doctor
and a manual single-record --commit of all three stages have been verified.
"""
