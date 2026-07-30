"""Native tool definition for the Orchestrator's planner.

The planner emits a DAG of agent steps. Instead of prompting for raw JSON and scraping it
back out with a regex + ``json.loads`` (which throws on any malformed reply), the model calls
this single ``create_plan`` tool and the provider guarantees a schema-valid plan — the ``agent``
and ``mode`` enums make an invalid value structurally impossible.

The returned ``arguments`` dict feeds ``OrchestratorAgent._plan_from_args`` unchanged, so the
whole downstream contract (execution_batches, HITL, synthesis) is untouched.
"""
from __future__ import annotations

from app.providers.tool_types import Tool

# Keep in sync with orchestrator.VALID_AGENTS.
_AGENT_ENUM = ["action_agent", "rag_agent", "research_agent", "conversational", "email_agent", "planner_agent"]

_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "stable, unique step id (e.g. 'facts', 'merge')"},
        "agent": {"type": "string", "enum": _AGENT_ENUM},
        "task": {"type": "string", "description": "self-contained instruction with all context the agent needs"},
        "mode": {"type": "string", "enum": ["read", "write", "synthesize"]},
        "depends_on": {"type": "array", "items": {"type": "string"},
                       "description": "ids of steps that must finish first; empty for independent steps"},
        "parallel_group": {"type": "string", "description": "optional group label for steps that may run together"},
        "verbatim": {"type": "boolean",
                     "description": "true = return this step's output exactly as-is with NO conversational "
                                    "synthesis step. Set true for personal-data logging/queries handled by "
                                    "action_agent (calorie logging, calories_remaining, set_calorie_budget, "
                                    "log_habit, add_todo) whose reply is already user-ready."},
    },
    "required": ["agent", "task"],
}

CREATE_PLAN = Tool(
    name="create_plan",
    description="Produce the minimum set of agent steps that fully handle the user's message.",
    parameters={
        "type": "object",
        "properties": {
            "reasoning": {"type": "string", "description": "one sentence on how the agents handle it"},
            "steps": {"type": "array", "items": _STEP_SCHEMA},
        },
        "required": ["steps"],
    },
)
