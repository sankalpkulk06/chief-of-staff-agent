# **Sage: Personal AI Chief-of-Staff**

**Wipro Junior FDE Assignment**  
 **GitHub:** https://github.com/sankalpkulk06/chief-of-staff-agent  
 **Live:** https://sage-2607286466.us-central1.run.app  
 **Credentials:** `testlive / testlive`  
 **Video Walkthrough:** \[Placeholder\]

## **1\. Multi-Agent Architecture**

Sage is a deployed personal AI chief-of-staff built as a 7-agent hierarchical system. The goal was to build more than a chatbot: Sage can break down a user’s request, route work to specialized agents, retrieve information, take safe actions, and show enough traceability for a user or reviewer to understand what happened.

I intentionally built the orchestration layer without relying on a black-box agent framework. A custom runner handles planning, dependency-aware execution, parallel read operations, write isolation, and safety checks. 

The main agents are:

* **OrchestratorAgent:** plans the workflow and synthesizes the final answer  
* **RAGAgent:** searches uploaded documents using pgvector  
* **ResearchAgent:** performs live web and news search using Tavily and DuckDuckGo fallbacks  
* **ActionAgent:** manages todos, habits, and saved facts  
* **EmailAgent:** connects to Gmail through per-user OAuth and triages inbox messages  
* **ConversationalAgent:** handles general conversation and response formatting  
* **SecurityAgent:** checks inputs before planning and scrubs outputs before delivery

The OrchestratorAgent uses Gemini 2.5 Flash to convert a natural-language request into structured `AgentStep` objects. Each step includes an `id`, execution `mode` (`read`, `write`, or `synthesize`), dependencies, and optional parallel grouping. The runner validates the plan, enforces an agent allowlist, limits execution to five steps, and builds execution batches. Independent read-only steps can run at the same time, while write actions and synthesis steps are isolated.

For example, if a user asks, “What are my tasks today and get me the latest cricket news?”, Sage creates one parallel batch where the ActionAgent reads todos while the ResearchAgent fetches news. A final synthesis step then combines both results into one response. Routing is driven by the orchestrator LLM rather than brittle keyword lists or regex-based intent routing.

Sage is deployed on GCP Cloud Run and also supports WhatsApp interaction through Twilio, with per-user session association and Twilio signature validation on the webhook.

## **2\. Security, Safety, and Guardrails**

Security is built into the pipeline rather than added as a separate feature. Before any agent planning happens, Sage runs the user input through the SecurityAgent. This input layer applies a per-user rate limit of 10 requests per minute, a 2000-character length limit, HTML sanitization, prompt injection checks, and an LLM-based fallback classifier.

The HTML sanitizer removes scripts, iframes, event handlers, forms, JavaScript URLs, and embedded objects after entity decoding. Prompt injection defense uses both deterministic patterns and an LLM classifier, which gives Sage two layers of protection. Potential PII such as SSNs, card numbers, phone numbers, and emails is flagged. Every security action, including blocks, sanitizations, PII flags, and secret redactions, is written to a Supabase `security_events` table for auditability.

After the agents complete their work, the output passes through another safety check. This layer redacts likely secrets such as API keys, bearer tokens, authorization headers, and credential-style environment values. Very long responses are trimmed before they reach the user.

The most important safety mechanism is human-in-the-loop approval for state-changing actions. The ActionAgent cannot directly execute `add_todo`, `add_habit`, `log_habit`, or `remember_fact`. Instead, Sage creates a `hitl_requests` record with a 10-minute expiry and shows the user a clear approval prompt in the web UI or WhatsApp. The write only happens after explicit approval.

This also works with parallel execution. If a write action is waiting for approval while an unrelated read-only task is already running, the read-only work can still finish. Sage stores the completed sibling output and returns it together with the approved write result. This creates a practical autonomy boundary: agents can retrieve, reason, and recommend, but users approve persistent state changes.

User data is isolated by design. Every session, document, fact, habit, Gmail token, and vector search is scoped to the authenticated `user_id`. Cross-user data leakage is prevented by architecture, not just by developer convention. Gmail access uses per-user OAuth tokens stored in Supabase rather than shared credentials.

## **3\. Implementation Approach**

Sage is built with Python, FastAPI, GCP Cloud Run, Supabase PostgreSQL with pgvector, sentence-transformers, Gemini, Groq, Twilio, and Gmail OAuth. The web app includes login, chat, file upload, source management, profile controls, HITL approval, and RAG evaluation badges. Local development uses SQLite and ChromaDB, while production uses Supabase and pgvector.

The custom runner owns the agent lifecycle. It validates the orchestrator plan, caps the number of steps, applies the agent allowlist, builds dependency batches, runs independent read-only work through `ThreadPoolExecutor`, isolates writes, handles timeouts, and returns structured `AgentResult` objects. I chose this approach over LangGraph or CrewAI because I wanted the execution model to be explicit, inspectable.

For RAG, uploaded documents are chunked, embedded into 384-dimensional vectors using `sentence-transformers/all-MiniLM-L6-v2`, and stored in pgvector. The RAGAgent first uses a fast LLM extraction call to identify the semantic query and optional filename. If a filename is detected, it becomes a hard SQL `WHERE` filter instead of being mixed into the embedding query. This improves precision when the user asks about a specific uploaded file. If retrieval is weak or empty, Sage can fall back to web search.

Reliability is handled through multiple fallback paths. Gemini can fall back to Groq if orchestration rate-limits. DuckDuckGo search has multiple backend fallbacks. pgvector uses separate read and write connections so a failed write does not corrupt the retriever connection state. Unit tests cover the security agent in depth (rate limiting, HTML sanitization, prompt-injection patterns, PII flagging, and output secret scrubbing), the SQLite registry, the Chroma and pgvector stores, the Ollama and HuggingFace provider clients, chunking and parsing, the scheduler, and parallel agent orchestration. End-to-end tests cover the ingest-then-ask RAG path. The API layer is currently thin on coverage — only the todos router is tested — and the Postgres registry used in production is exercised only indirectly; both are the next gaps worth closing.

## **4\. Use of AI/LLMs and Collaboration**

Different LLMs are used for different responsibilities. Gemini 2.5 Flash handles high-level orchestration, structured planning, dependency reasoning, and final synthesis. Groq Llama 3.3 70B handles faster sub-agent work such as summarization, extraction, email triage, and action parsing. The same sub-agent provider is also used for inline LLM-as-judge evaluation.

Every successful RAG answer is scored for **faithfulness** and **answer relevancy**. Faithfulness checks whether the answer is grounded in retrieved chunks. Answer relevancy checks whether the response directly answers the user’s question. These scores are shown as a badge in the UI, giving users a lightweight signal about answer quality. The evaluator is implemented directly against the existing LLM provider with a 5-second timeout and graceful fallback, avoiding a heavy external evaluation dependency.

The main design trade-off is autonomy versus control. The orchestrator is allowed to decide how to route and decompose a request, but execution is constrained through an agent allowlist, step caps, output scrubbing, user-level isolation, and HITL approval for writes. This keeps the assistant useful without allowing it to silently modify private user state.

## **5\. Sample Prompts**

Some sample prompts:

1. “*My name is Sankalp Kulkarni*” \- saves facts  
2. “*Remind me to review the multi-agent architecture tonight at 7pm*” \- add tasks  
3. “*I want to start tracking my daily workout habit*” \- adds habits  
4. “*With a quick search what is CrewAI*” \- web search  
5. “*What's happening in the world today?*” \- news search  
6. “*With a quick search tell me what CrewAI is and also get me the news on AI agent frameworks”* \- multi-agent (web search and news)  
7. “check my emails and tell me what needs my attention” \- email triage   
8. “*Search for what CrewAI does and remind me to try it this weekend*” \- web search and action  
9. “*Search for what Wipro does as a company, and remind me to read about it tonight*” \- web search and action  
10. “*With a quick search tell me what CrewAI is, also search for what LangGraph does, and also get me the latest news on AI agent frameworks*” \- web search, web search and news 

## **6\. Limitations and Future Work**

* Deployment uses a GitHub Actions workflow triggered manually through `workflow_dispatch`; automatic deployment on every push is a future hardening step.   
* Gmail OAuth works, but the Google app is still in testing mode, so only approved test users can connect.   
* Rate limiting is currently in-memory, which is acceptable for the demo but should move to Redis for a multi-instance production deployment.  
* RAG evaluation scores are shown in the response but are not yet persisted for historical trend analysis. Retrieval quality is evaluated through answer grounding, but a labeled evaluation set with Precision@K, Recall@K, MRR, or NDCG would make retrieval improvements more measurable. 

