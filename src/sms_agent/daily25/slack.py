"""Run summaries for all three daily-35 stages, reusing escalate.alert's
existing "daily outreach summary" channel rule (kind="campaign" is already in
ALWAYS_POST) instead of adding a new alert kind for one report.
"""
from __future__ import annotations


def post_select_summary(summary: dict) -> None:
    from .. import escalate

    title = (
        f"Daily 35 select ({summary['date']}): {summary['picked']} picked, "
        f"{summary['tasks_ok']} tasks, {summary['precall_drafted']} pre-call texts drafted"
    )
    detail = (
        f"Board mix: {summary['board_mix']}\n"
        f"Candidates considered: {summary['candidates']}\n"
        f"Cooldown-held (already texted within {summary.get('cooldown_days', '')}d): "
        f"{summary['cooldown_held']}\n"
        f"Phones newly Trestle-scored: {summary['phones_scored']}\n"
        f"Errors: {summary['errors']}"
    )
    escalate.alert(title, detail, kind="campaign")


def post_send_summary(summary: dict) -> None:
    from .. import escalate

    title = f"Daily 35 send ({summary['date']}): {summary['queued']} pre-call texts queued"
    detail = (
        f"Skipped: {summary['skipped']} ({summary.get('skip_reasons', '')})\n"
        f"Span: {summary.get('span', '')}\n"
        f"Errors: {summary['errors']}"
    )
    escalate.alert(title, detail, kind="campaign")


def post_voicemail_summary(summary: dict) -> None:
    from .. import escalate

    title = (
        f"Daily 35 voicemail follow-up ({summary['date']}): "
        f"{summary['queued']} follow-up texts queued"
    )
    detail = (
        f"Left Voicemail today: {summary['left_voicemail']}\n"
        f"Skipped: {summary['skipped']} ({summary.get('skip_reasons', '')})\n"
        f"Errors: {summary['errors']}"
    )
    escalate.alert(title, detail, kind="campaign")
