"""LLM-as-judge evaluation for RAG faithfulness and answer relevancy."""
import concurrent.futures
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger(__name__)

_FAITHFULNESS_PROMPT = """\
You are an evaluation judge.

Question: {question}
Answer: {answer}

Retrieved document contexts:
{contexts}

Task: Score how faithful the answer is to the retrieved contexts.
- 1.0 = every claim in the answer is directly supported by the contexts
- 0.0 = the answer contradicts or ignores the contexts entirely
- Partial credit for partially supported answers

Respond with ONLY valid JSON, no prose:
{{"faithfulness": <float 0.0-1.0>}}"""

_RELEVANCY_PROMPT = """\
You are an evaluation judge.

Question: {question}
Answer: {answer}

Task: Score how relevant and complete this answer is to the question.
- 1.0 = directly and completely answers what was asked
- 0.0 = completely off-topic or ignores the question
- Partial credit for partial answers

Respond with ONLY valid JSON, no prose:
{{"answer_relevancy": <float 0.0-1.0>}}"""


@dataclass
class RagasResult:
    evaluated: bool
    faithfulness: Optional[float] = None
    answer_relevancy: Optional[float] = None
    contexts_count: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "evaluated": self.evaluated,
            "faithfulness": self.faithfulness,
            "answer_relevancy": self.answer_relevancy,
            "contexts_count": self.contexts_count,
            "error": self.error,
        }


class RagasService:
    def __init__(self, chat_provider) -> None:
        self._provider = chat_provider

    def _call(self, messages: list, key: str) -> Optional[float]:
        response = self._provider.chat(messages)
        cleaned = re.sub(r"```(?:json)?", "", response).strip().rstrip("`").strip()
        match = re.search(r"\{.*?\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError("no JSON object in response")
        data = json.loads(match.group())
        val = data.get(key)
        if val is None:
            raise ValueError(f"key '{key}' missing from response")
        return max(0.0, min(1.0, float(val)))

    def score(
        self,
        question: str,
        answer: str,
        contexts: List[str],
        timeout: float = 5.0,
    ) -> RagasResult:
        if not contexts:
            return RagasResult(evaluated=False, contexts_count=0, error="no_contexts")

        contexts_count = len(contexts)
        formatted = "\n".join(f"[{i + 1}] {ctx}" for i, ctx in enumerate(contexts))

        faith_msgs = [{"role": "user", "content": _FAITHFULNESS_PROMPT.format(
            question=question, answer=answer, contexts=formatted,
        )}]
        rel_msgs = [{"role": "user", "content": _RELEVANCY_PROMPT.format(
            question=question, answer=answer,
        )}]

        faithfulness: Optional[float] = None
        answer_relevancy: Optional[float] = None
        errors: list = []

        deadline = time.monotonic() + timeout

        # Don't use `with` — it blocks on __exit__ until all threads finish.
        # shutdown(wait=False) lets timed-out threads run to completion in the
        # background without blocking the response.
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        try:
            faith_fut = pool.submit(self._call, faith_msgs, "faithfulness")
            rel_fut = pool.submit(self._call, rel_msgs, "answer_relevancy")

            for fut, slot in ((faith_fut, "faith"), (rel_fut, "rel")):
                remaining = max(0.05, deadline - time.monotonic())
                try:
                    val = fut.result(timeout=remaining)
                    if slot == "faith":
                        faithfulness = val
                    else:
                        answer_relevancy = val
                except concurrent.futures.TimeoutError:
                    errors.append("timeout")
                    log.debug("RagasService: %s timed out", slot)
                except (json.JSONDecodeError, ValueError) as exc:
                    errors.append("parse_error")
                    log.debug("RagasService: %s parse error: %s", slot, exc)
                except Exception as exc:
                    errors.append("llm_error")
                    log.debug("RagasService: %s llm error: %s", slot, exc)
        finally:
            pool.shutdown(wait=False)

        evaluated = faithfulness is not None or answer_relevancy is not None
        return RagasResult(
            evaluated=evaluated,
            faithfulness=faithfulness,
            answer_relevancy=answer_relevancy,
            contexts_count=contexts_count,
            error=errors[0] if errors and not evaluated else None,
        )
