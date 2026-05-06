"""
Lessons updater — Claude Sonnet reads the last N completed trade journals
and appends a structured lessons entry to journal/lessons.md.

Called by the EOD routine after any session that had at least one completed trade.
"""

import logging
import os
from datetime import date
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from journal.writer import get_recent_journals

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
log = logging.getLogger(__name__)

_MODEL         = "claude-opus-4-7"
_LESSONS_FILE  = Path(__file__).parent / "lessons.md"
_N_JOURNALS    = 5


_SYSTEM_PROMPT = """\
You are a trading coach reviewing a quantitative trader's recent trade journals.
Your job is to extract actionable lessons — specific patterns that worked, specific mistakes to avoid,
and concrete rules to adjust.

Be direct and specific. No vague advice like "be patient" or "manage risk."
Instead: "RSI > 75 at entry led to 3 losses — add RSI ≤ 70 as a hard filter."

Output a concise markdown section (no heading — the caller adds the heading) with:
- 2–4 bullet points: what worked (cite the specific trade)
- 2–4 bullet points: what to improve (cite the specific trade)
- 1–2 bullet points: rule changes or parameter tweaks to consider
"""


def update_lessons(journals: list[str] | None = None) -> str:
    """
    Generate and append a lessons entry to lessons.md.
    Returns the new lessons text, or empty string on failure.
    """
    if journals is None:
        journals = get_recent_journals(_N_JOURNALS)

    if not journals:
        log.info("No completed trade journals found — skipping lessons update")
        return ""

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — skipping lessons update")
        return ""

    journal_text = "\n\n---\n\n".join(journals)
    user_prompt  = (
        f"Here are the last {len(journals)} completed trade journal(s):\n\n"
        f"{journal_text}\n\n"
        "Please extract lessons as described."
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=800,
            system=[{
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_prompt}],
        )
        lessons_text = response.content[0].text.strip()
    except Exception as e:
        log.error("Claude lessons generation failed: %s", e)
        return ""

    # Append to lessons.md under a dated heading
    today = date.today().isoformat()
    heading = f"\n\n---\n\n## Session: {today}\n\n"
    entry = heading + lessons_text + "\n"

    _LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_LESSONS_FILE, "a", encoding="utf-8") as f:
        f.write(entry)

    log.info("Lessons updated: %s (%d chars)", _LESSONS_FILE, len(entry))
    return lessons_text
