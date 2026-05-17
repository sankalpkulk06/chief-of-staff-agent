from typing import List, Optional

from pydantic import BaseModel, Field

from app.providers.ollama_chat import OllamaChatProvider
from app.retrieval.prompt_builder import build_grounded_prompt
from app.retrieval.retriever import RetrievedChunk, RetrievalResult, Retriever


class QAResult(BaseModel):
    question: str
    answer: str
    sources: List[RetrievedChunk] = Field(default_factory=list)
    retrieval: RetrievalResult
    prompt: str
    sources_used: bool = Field(default=True)
    news_sources: List[dict] = Field(default_factory=list)
    web_sources: List[dict] = Field(default_factory=list)
    steps: List[dict] = Field(default_factory=list)  # agent execution steps


class QAService:
    def __init__(self, retriever: Retriever, chat_provider: OllamaChatProvider, max_prompt_chunks: int = 5):
        self._retriever = retriever
        self._chat_provider = chat_provider
        self._max_prompt_chunks = max_prompt_chunks

    def answer_question(self, question: str, top_k: Optional[int] = None) -> QAResult:
        retrieval = self._retriever.retrieve(question=question, top_k=top_k)
        if retrieval.is_empty:
            prompt = build_grounded_prompt(
                question=question,
                chunks=[],
                max_chunks=self._max_prompt_chunks,
            )
            return QAResult(
                question=question,
                answer="I don't know based on the provided documents.",
                sources=[],
                retrieval=retrieval,
                prompt=prompt,
            )
        prompt = build_grounded_prompt(
            question=question,
            chunks=retrieval.chunks,
            max_chunks=self._max_prompt_chunks,
        )
        answer = self._chat_provider.generate(prompt=prompt)
        return QAResult(
            question=question,
            answer=answer,
            sources=retrieval.chunks,
            retrieval=retrieval,
            prompt=prompt,
        )
