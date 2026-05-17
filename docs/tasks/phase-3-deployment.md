# Phase 3 — Deployment

Deploy Sage to a cloud provider so the system is accessible via a live public endpoint.
The assignment requires a public URL (hosted UI or API).

**Target: Google Cloud Run** (preferred per assignment, free tier generous, Docker-native)
**Fallback: Railway** (fastest path, zero infra config, GitHub-connected)

---

## 3.1 Prepare the Docker Image for Cloud

**Tasks:**
- [ ] Update `Dockerfile`:
  - Remove Ollama dependency (no local model server in cloud)
  - Set `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]`
  - Use `PORT` env var (Cloud Run injects this): `--port ${PORT:-8080}`
  - Multi-stage build: builder stage installs deps, runtime stage is slim
  - Ensure image size is under 1 GB (remove data/ volume mounts from prod image)
- [ ] Update `docker-compose.yml` to add `OLLAMA_ENABLED=false` for cloud profile
- [ ] Add `.dockerignore`: exclude `data/`, `.env`, `*.pyc`, `__pycache__`, `.git`, `tests/`
- [ ] Verify `app/main.py` is a proper FastAPI app entry point (not just the webhook server)
  - Confirm it imports and mounts the router correctly
  - Serve the frontend static files from `/` 

---

## 3.2 Deploy to Google Cloud Run

**Tasks:**
- [ ] Create a GCP project (or use existing)
- [ ] Enable APIs: Cloud Run, Artifact Registry, Cloud Build
- [ ] Build and push image to Artifact Registry:
  ```bash
  gcloud builds submit --tag gcr.io/PROJECT_ID/sage-agent
  ```
- [ ] Deploy to Cloud Run:
  ```bash
  gcloud run deploy sage-agent \
    --image gcr.io/PROJECT_ID/sage-agent \
    --platform managed \
    --region us-central1 \
    --allow-unauthenticated \
    --port 8080 \
    --memory 1Gi \
    --set-env-vars "LLM_PROVIDER=groq,STORAGE_BACKEND=postgres,..."
  ```
- [ ] Set all required env vars via Cloud Run environment configuration
- [ ] Verify the deployed URL returns a working frontend and `/api/v1/` responds
- [ ] Note the public URL (format: `https://sage-agent-XXXXX-uc.a.run.app`)

---

## 3.3 Alternative: Railway (Faster Path)

If GCP setup takes too long before the deadline, Railway as fallback:

**Tasks:**
- [ ] Push repo to public GitHub
- [ ] Connect Railway to the GitHub repo
- [ ] Set all env vars in Railway dashboard
- [ ] Railway auto-detects Dockerfile and deploys
- [ ] Get the generated Railway URL (format: `https://sage-agent.up.railway.app`)

---

## 3.4 Verify Live Deployment

**Tasks:**
- [ ] Hit `GET /` — frontend HTML loads
- [ ] Hit `POST /api/v1/auth/login` with correct passphrase — returns session token
- [ ] Send a chat message via the web UI — Orchestrator routes it, LLM responds
- [ ] Test RAG: ingest a document, then ask a question about it
- [ ] Test multi-agent routing: ask a question that triggers web search
- [ ] Verify Security Agent blocks a basic prompt injection attempt
- [ ] Check logs in Cloud Run console for any errors

---

## 3.5 Make the GitHub Repo Public

**Tasks:**
- [ ] Go to GitHub repo settings → Change visibility to Public
- [ ] Ensure no secrets are committed (`.env` is gitignored, credentials are gitignored)
- [ ] Verify `data/credentials/` is gitignored
- [ ] Run `git log --all -- '*.env'` to make sure `.env` was never committed
- [ ] Add the live deployment URL to `README.md`
