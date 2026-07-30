"""Shared data structures for the multi-agent system."""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentStep:
    """A single step in the orchestrator's plan."""
    agent: str   # "rag_agent" | "research_agent" | "action_agent" | "conversational"
    task: str    # Natural language description of what this agent should do
    id: str = ""
    depends_on: list[str] = field(default_factory=list)
    parallel_group: Optional[str] = None
    mode: str = "read"  # "read" | "write" | "synthesize"
    verbatim: bool = False  # return this step's output as-is; never reword via synthesis


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

    def execution_batches(self, max_parallel: int = 3) -> list[list[AgentStep]]:
        """
        Return dependency-ordered batches.

        Steps within the same batch may run concurrently. Writes and synthesis are
        intentionally isolated unless a future policy proves a narrower conflict
        boundary is safe.
        """
        if not self.steps:
            return []

        max_parallel = max(1, max_parallel)
        by_id = {step.id: step for step in self.steps if step.id}
        scheduled: set[str] = set()
        batches: list[list[AgentStep]] = []

        while len(scheduled) < len(self.steps):
            ready = [
                step
                for step in self.steps
                if step.id not in scheduled
                and all(dep in scheduled or dep not in by_id for dep in step.depends_on)
            ]
            if not ready:
                # Malformed dependency cycle: preserve progress by running the next
                # unscheduled step alone.
                ready = [next(step for step in self.steps if step.id not in scheduled)]

            if ready[0].mode in {"write", "synthesize"}:
                batch = [ready[0]]
            else:
                batch = [
                    step
                    for step in ready
                    if step.mode not in {"write", "synthesize"}
                ][:max_parallel]

            batches.append(batch)
            scheduled.update(step.id for step in batch)

        return batches


@dataclass
class SecurityResult:
    """Output from the SecurityAgent's input or output check."""
    blocked: bool
    reason: Optional[str] = None     # "prompt_injection" | "length_exceeded" | "rate_limit_exceeded"
    flags: list = field(default_factory=list)  # e.g. ["pii_detected"]
    sanitized_input: Optional[str] = None  # non-None if HTML was stripped; caller uses this instead of original
