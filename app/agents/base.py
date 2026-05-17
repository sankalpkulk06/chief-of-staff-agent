"""Shared data structures for the multi-agent system."""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentStep:
    """A single step in the orchestrator's plan."""
    agent: str   # "rag_agent" | "research_agent" | "action_agent" | "conversational"
    task: str    # Natural language description of what this agent should do


@dataclass
class AgentResult:
    """Output from a single agent execution."""
    agent: str
    task: str
    output: str
    success: bool
    error: Optional[str] = None
    citations: list = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrchestratorPlan:
    """The orchestrator's decomposition of a user query into agent steps."""
    steps: list[AgentStep]
    reasoning: str = ""
