"""Native tool + prompt for passive fact extraction.

After a turn, the learner asks the model to surface DURABLE facts about the USER that were
revealed in the exchange (things the user said, or that an email/document made clear). The
model returns them as structured ``record_facts`` calls; a prompt-for-JSON fallback covers
providers without tool-calling.
"""
from __future__ import annotations

from app.providers.tool_types import Tool

_FACT_ITEM = {
    "type": "object",
    "properties": {
        "content": {"type": "string",
                    "description": "the fact in concise third person, e.g. \"user's father is Naveen\", "
                                   "\"user works at Acme\", \"user lives in Austin\""},
        "subject": {"type": "string",
                    "description": "short noun-phrase the fact is ABOUT, e.g. \"father\", \"employer\", "
                                   "\"home city\", \"name\" — used to dedupe/supersede earlier facts"},
        "category": {"type": "string", "enum": ["personal", "work"]},
        "confidence": {"type": "number", "description": "0-1: how sure this is a real, correct fact"},
        "durable": {"type": "boolean",
                    "description": "true only if stable over time (identity, relationships, employer, home, "
                                   "lasting preferences). false for one-off events, moods, today-only things"},
        "about_user": {"type": "boolean",
                       "description": "true only if the fact is about the USER themselves — not world trivia, "
                                      "news, or other people in isolation"},
    },
    "required": ["content", "subject", "category", "confidence", "durable", "about_user"],
}

RECORD_FACTS = Tool(
    name="record_facts",
    description="Record every durable fact about the USER revealed this turn. Return an empty list "
                "if nothing durable and user-specific was revealed.",
    parameters={"type": "object", "properties": {"facts": {"type": "array", "items": _FACT_ITEM}},
                "required": ["facts"]},
)

LEARN_SYSTEM = (
    "You extract durable, user-specific facts worth remembering long-term from a conversation turn.\n"
    "Only record facts that are (1) DURABLE (stable over time — identity, relationships, employer, "
    "home, lasting preferences), and (2) ABOUT THE USER themselves.\n"
    "Do NOT record: one-off events or activities, moods, tasks/reminders, calorie/meal logs, world "
    "trivia, news, or facts about other people that aren't really about the user.\n"
    "Facts may come from what the user said OR from what an email/document revealed about the user. "
    "Be conservative: if unsure whether something is durable or correct, give it low confidence.\n"
    "Return an empty list when nothing qualifies."
)
