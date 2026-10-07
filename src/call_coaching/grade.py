"""grade.py - grade one transcribed call against a coaching rubric with Claude.

The unattended counterpart to the Claude-session grading in HANDOFF.md and
DAILY_AUDIT_PROMPT.md: same three rubrics (skills/<skill>/references/rubric.md),
same "every observation cites the transcript" rule, but one API call per call
with a fixed JSON shape, so a scheduled job can run it with nobody watching.

The rubric is the system prompt and is cached, so a run that grades several
calls against the same rubric pays for it once.

Env:
  ANTHROPIC_API_KEY       required
  CALL_COACH_MODEL        default claude-opus-5-5
  CALL_COACH_EFFORT       default medium (low | medium | high | xhigh | max)

USAGE (one call, for testing):
  python src/call_coaching/grade.py --transcript output/call_coaching/transcripts/<id>.md --skill cold-call-coach
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = ROOT / "skills"
MODEL = os.environ.get("CALL_COACH_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("CALL_COACH_EFFORT", "medium")

SKILLS = ("cold-call-coach", "lead-manager-coach", "closer-coach")

INSTRUCTIONS = """You grade real estate acquisition phone calls for a sales team's daily coaching review.
The rubric below is the scoring standard. Apply it exactly: the applicability gate, the auto-fail
scan, short-call handling, category scores 0-5, the weighted total, and the grade band.

Rules:
- Every strength and every improvement must be grounded in something said on the call. Quote it.
- Transcript speaker labels can be wrong. Decide who our agent is by content (the person asking
  about buying the property), and grade that person.
- If the gate says the call should not be scored (voicemail, wrong number, no real exchange),
  set report_type to "not_scored", score to 0, and say why in call_summary.
- For a true short call (a decline inside roughly 30 seconds), set report_type to "short" and
  score to 0; do not compute a weighted total. Grade the reachable criteria in category_scores.
- call_summary, did_well and improve are read by the rep on a spreadsheet and in Slack: plain
  sentences, one or two each, specific enough to act on. No generic praise.

RUBRIC:
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "report_type": {"type": "string", "enum": ["full", "short", "not_scored"]},
        "score": {"type": "integer", "description": "weighted total 0-100 for full calls, else 0"},
        "grade_band": {"type": "string", "description": "the rubric's band name, or N/A"},
        "auto_fail": {"type": "boolean"},
        "auto_fail_reason": {"type": "string", "description": "empty unless auto_fail"},
        "call_summary": {"type": "string"},
        "did_well": {"type": "string"},
        "improve": {"type": "string"},
        "category_scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "score": {"type": "integer", "description": "0-5, or -1 if not reachable on this call"},
                    "evidence": {"type": "string", "description": "short verbatim quote"},
                },
                "required": ["category", "score", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["report_type", "score", "grade_band", "auto_fail", "auto_fail_reason",
                 "call_summary", "did_well", "improve", "category_scores"],
    "additionalProperties": False,
}

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def rubric(skill: str) -> str:
    if skill not in SKILLS:
        skill = "cold-call-coach"
    return (SKILLS_DIR / skill / "references" / "rubric.md").read_text(encoding="utf-8")


def grade(transcript: str, skill: str, context: str = "") -> dict:
    """Grade one call. Returns the SCHEMA dict plus `model`.

    Raises RuntimeError when the model declines or the output is cut off, so
    the caller can log that call as failed instead of writing a bad row.
    """
    user = (f"CALL CONTEXT:\n{context}\n\n" if context else "") + f"TRANSCRIPT:\n{transcript}"
    resp = _get_client().beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=[{"type": "text", "text": INSTRUCTIONS + rubric(skill),
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"model declined to grade (category: "
                           f"{getattr(resp.stop_details, 'category', None)})")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("grading output hit max_tokens")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    result = json.loads(text)
    result["model"] = resp.model
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Grade one transcript against a coaching rubric")
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--skill", default="cold-call-coach", choices=SKILLS)
    args = ap.parse_args()
    result = grade(Path(args.transcript).read_text(encoding="utf-8"), args.skill)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
