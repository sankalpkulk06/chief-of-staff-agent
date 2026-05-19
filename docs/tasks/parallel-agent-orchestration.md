# Parallel Agent Orchestration Plan

**Goal:** When the orchestrator decomposes a user request into independent work, Sage should run those agent steps concurrently, then merge their outputs into one polished response. Dependent steps should still run in sequence.

**Target example:**

> "What are my tasks for today, and also get the latest news on IPL?"

Expected execution shape:

1. Security checks the input.
2. Orchestrator plans two independent retrieval tasks plus one synthesis task.
3. `action_agent` retrieves today's tasks while `research_agent` fetches IPL news.
4. Orchestrator synthesizes both outputs into one clear answer.
5. Security scrubs the final output.

---

## Product Behavior

- Run mutually exclusive work in parallel whenever results do not depend on each other.
- Preserve sequential execution when a step needs previous step output.
- Keep `conversational` synthesis as the final step after tool-using agents complete.
- Preserve HITL behavior: if any parallel action triggers approval, stop the remaining synthesis path and return the approval request.
- Show trace events in a readable order so the user sees parallel work as intentional, not noisy.
- Never parallelize unsafe side effects against the same state boundary without explicit dependency grouping.

---

## Dependency Rules

Independent by default:

- `action_agent` read task plus `research_agent` read task.
- `rag_agent` document search plus `research_agent` live research.
- `email_agent` inbox triage plus `research_agent` live research.
- Multiple `research_agent` read-only tasks, subject to the existing rate limit.

Sequential by default:

- Any `conversational` synthesis step after tool-using agents.
- Any step whose task references "the above", "those results", "that answer", or previous step output.
- RAG fallback to research when document relevance is low.
- Action write operations that mutate todos, habits, facts, reminders, or email state.
- Any HITL-triggering action.

Conservative conflict model:

- Treat `action_agent` writes as exclusive.
- Allow `action_agent` reads to run with read-only agents.
- Keep per-agent concurrency caps so one user turn cannot flood web/news/email services.

---

## Implementation Plan

### 1. Extend the Plan Schema

- Update `AgentStep` in `app/agents/base.py` with:
  - `id: str`
  - `depends_on: list[str]`
  - `parallel_group: str | None`
  - `mode: "read" | "write" | "synthesize"`
- Keep backward compatibility by defaulting missing fields when parsing old plans.
- Update `OrchestratorPlan` with a helper that returns executable batches in dependency order.

### 2. Update the Orchestrator Prompt

- Replace the current compound rule, "action_agent first -> research_agent second -> conversational last", with dependency-aware routing.
- Instruct the model to mark independent steps with empty `depends_on` and the same `parallel_group`.
- Require `conversational` to depend on every prior non-conversational step.
- Add examples for:
  - tasks today + IPL news
  - document summary + latest related news
  - create reminder + find tutorials
  - read habits + check inbox

### 3. Parse and Validate Dependencies

- Update `OrchestratorAgent._parse_plan()` to normalize IDs and dependencies.
- Drop dependencies pointing to unknown or invalid steps.
- Force `conversational` synthesis to depend on all earlier tool steps if the plan omitted dependencies.
- Force write steps to their own batch unless explicitly proven safe.
- Keep `_MAX_STEPS` as the guardrail.

### 4. Build the Parallel Executor

- Refactor the loop in `AgentRunner.run()` into:
  - `_execute_step(...)`
  - `_execute_batch_concurrently(...)`
  - `_build_execution_batches(plan)`
- Use `concurrent.futures.ThreadPoolExecutor` or `asyncio.to_thread` around existing synchronous agents.
- Preserve result ordering by original plan order, even if tasks finish out of order.
- Give each step a timeout and return a structured `AgentResult` on timeout.
- Limit concurrent steps per turn with a small cap, initially `3`.

### 5. Handle Shared State Safely

- Give each parallel step its own immutable snapshot of:
  - `question`
  - truncated `history`
  - previous completed batch results
- Do not let steps in the same batch see each other's partial results.
- After a batch completes, append results in plan order before starting dependent batches.
- Keep RAG fallback local to the RAG step, so fallback does not race with planned research unless the planned research is already independent.

### 6. Preserve HITL Semantics

- If any step returns `metadata.hitl_pending`, cancel or ignore unfinished sibling outputs when possible.
- Return the HITL message immediately.
- Emit a trace event explaining that synthesis is paused for approval.
- Do not synthesize a response that implies the pending action completed.

### 7. Upgrade Tracing

- Add optional metadata to `TraceEvent` and `TraceBroker.make_callback()`:
  - `step_id`
  - `batch_id`
  - `parallel_group`
  - `agent`
  - `started_at`
  - `completed_at`
- Emit:
  - `[Plan] 3 steps planned`
  - `[Batch] Running 2 independent steps`
  - `[Act] list_facts: retrieve all tasks due today`
  - `[Web] fetch_news: latest news on IPL`
  - `[Merge] Synthesizing results`
- Keep the current simple type/status/message shape for frontend compatibility.

### 8. Synthesis Contract

- Update `orchestrator_synthesis.txt` to tell Sage to:
  - preserve all successful independent results
  - clearly separate unrelated domains
  - mention partial failures only when they affect the answer
  - avoid making one agent's failure hide another agent's success
- Pass structured agent result metadata into synthesis, not just concatenated text, once tests cover the existing behavior.

### 9. Frontend Display

- Keep the current step stream working.
- Add grouped display when trace metadata contains a shared `batch_id`.
- Show parallel siblings as simultaneous rows under one batch label.
- Continue to render older traces as a flat list.

### 10. Tests

- Unit test plan parsing:
  - old JSON without dependency fields still works
  - independent steps get stable IDs
  - conversational gets inferred dependencies
  - invalid dependency references are dropped
- Unit test batch building:
  - tasks today + IPL news creates one parallel batch plus synthesis
  - reminder write + tutorial search keeps write isolated or ordered according to policy
  - dependent conversational runs last
- Integration test runner concurrency with fake slow agents:
  - two independent 500 ms agents finish in less than 900 ms
  - final result order follows plan order
  - one failed sibling still allows the other result into synthesis
- Regression test HITL:
  - pending approval stops synthesis and does not present sibling output as final completion.

### 11. Rollout

- Add a setting:
  - `agent_parallelism_enabled: bool = True`
  - `agent_parallelism_max_workers: int = 3`
- Keep a sequential fallback path behind the feature flag.
- Log per-turn latency and batch count so the speedup is visible.
- Start with read-only parallelism, then expand once write conflict tests are solid.

### 12. Acceptance Criteria

- The example prompt plans as:
  - input safety
  - parallel batch: `action_agent` reads today's tasks, `research_agent` fetches IPL news
  - synthesis
  - output scrub
- The two independent agent calls actually overlap in wall-clock time.
- User-visible traces make the parallel batch understandable.
- Final response combines both outputs cleanly.
- Existing chat, CLI, REST, WhatsApp, RAG fallback, and security tests still pass.
