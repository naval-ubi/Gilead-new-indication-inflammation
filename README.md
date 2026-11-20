# Inflammation – New Indication Platform · FastAPI backend

Backend for the attached prototype (`Mon_16_Nov_New_Indication.html`). The prototype keeps all state in
JavaScript arrays and `localStorage`; this project replaces each of those with a real API.

## 1. What the prototype tells us the backend must do

| UI module | What it really needs from the server |
|---|---|
| **Home** | Greeting, ask-box, three tables (In Progress / Recent Results / Saved) with module filter, sort, duplicate, delete |
| **AI Search** | Async research job (steps + progress), Deep Research mode, source selection, follow-ups, AI prompt suggestion, 5 result tabs, save/share/export |
| **Drug Pipeline Explorer** | Job + live filtering over an asset catalogue; 5 views (table, swimlane, graph, heatmap, radar) and KPI tiles |
| **CIOA** | Multi-agent job (one step per capability) producing a sectioned report; approve; export |
| **Prioritization Scoring** | Criteria + weights (=100%), AI scores, overrides **with justification**, scenarios, sensitivity |
| **Workspace** | Saved items, folders, sharing (view/edit), versions + restore, notes, archive |
| **History** | Search history + research activity timeline, KPI tiles, **Resume** failed/paused runs |
| **Data Source / Admin** | Connector registry (enable/test/health), users & roles, workflows, templates, prompts, scoring models, audit log |
| **Header/platform** | Research context chip, notifications, help chat, preferences |

## 2. Architecture

```
React/Vue SPA ──HTTPS/JWT──▶ FastAPI (app/main.py, /api/v1)
                               ├─ routers/   thin HTTP layer + validation + permissions
                               ├─ services/  scoring.py, pipeline.py  (PURE, unit-tested)
                               │             orchestrator.py          (resumable run engine)
                               │             integrations.py          (LLMClient + Connector seams)
                               └─ db/        SQLAlchemy 2.0 → PostgreSQL (SQLite for dev)
Background: FastAPI BackgroundTasks now → Arq/Celery worker later (same execute_run(run_id))
```

Key decisions

1. **One generic `Run` job model for all three research modules.** `POST /runs` → `202` → poll
   `GET /runs/{id}` or stream `GET /runs/{id}/events` (SSE). Step list, progress, partial output and errors are
   persisted after every step, which gives Cancel, Pause, **Resume** (History) and failure isolation for free.
2. **Saved ≠ rerun.** `POST /runs/{id}/save` freezes question + context + result into `WorkspaceItem.snapshot`;
   reopening returns the snapshot (matches "Reopening does not rerun the research").
3. **Scoring math is a pure module** (`services/scoring.py`) – identical to the prototype's formula, tested.
4. **Everything external is behind two interfaces** (`LLMClient`, `BaseConnector`). Stubs ship so the API runs
   offline; one real class per source replaces each stub. A failing connector never fails a run.
5. **Admin switches are enforced server-side**: a disabled connector is excluded from every query.
6. **RBAC** by role → permission set (`core/deps.py`), plus per-item ownership/share checks in Workspace.
7. **Audit log** for admin edits, overrides, approvals, deletes (before/after JSON).

## 3. API map (prefix `/api/v1`)

```
POST /auth/login            GET /me                 PUT /me/context   PUT /me/preferences
GET  /notifications         POST /notifications/read-all   POST /notifications/{id}/read
POST /help/chat

GET  /home?tab=in_progress|recent_results|saved&module=&sort=&desc=
POST /home/runs/{id}        {action: duplicate|delete}

POST /prompts/suggest       GET /prompts/suggested
POST /runs                  {module: ai|pipeline|cioa, question, deep_research, sources, filters, capabilities}
GET  /runs/{id}  /result  /events(SSE)      POST /runs/{id}/cancel|pause|resume|rerun|save|approve
GET  /pipeline/options      GET /pipeline/assets?target=&indication=&modality=&stage=&view=table|swimlane|graph|heatmap|radar
GET  /cioa/capabilities

GET  /workspace/items?library=all|my|shared|archived&type=&module=&q=&sort=
GET/PATCH/DELETE /workspace/items/{id}      POST .../duplicate  .../share  .../notes  .../versions/{vid}/restore
GET  /users/directory

GET  /history/summary       GET /history/searches       GET /history/activity      DELETE /history?scope=

GET  /scoring/models        POST /scoring/assessments   GET /scoring/assessments/{id}
PUT  .../weights            POST/PATCH/DELETE .../criteria      POST .../reset
PUT  .../overrides          GET .../sensitivity         POST/GET .../scenarios (+ /{sid}/load, DELETE)
POST .../save-to-workspace

GET  /datasources           POST /datasources    PATCH /datasources/{id}   POST .../toggle?enabled=   POST .../test
GET  /admin/overview  /admin/users  /admin/records/{workflow|template|prompt}  /admin/scoring-models  /admin/audit
```

Interactive docs: `http://localhost:8000/docs`.

## 4. Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload          # seeds demo data on first start
pytest                                  # test_scoring.py is framework-free; test_api.py needs the install
```
Demo logins (password `ChangeMe123!`): `swanik@example.com` (Analyst), `maria.chen@example.com` (Admin),
`sam.patel@example.com` (Reviewer).

## 5. What is real vs. stubbed (be aware before going live)

| Area | State |
|---|---|
| Scoring, pipeline aggregations, weights validation, overrides, scenarios, sensitivity | Implemented + unit-tested |
| Auth (JWT), RBAC, audit, workspace sharing/versions, history, admin CRUD, connector toggle | Implemented |
| Run engine (steps, cancel/pause/resume, notifications, SSE) | Implemented |
| **LLM answers, evidence retrieval, CIOA section content, AI scores, exports (PDF/PPT/Excel)** | **Stubs** – wire `integrations.py`, `orchestrator.build_*`, `scoring.ai_recommend_scores` |
| Connector clients (PubMed, Citeline, IQVIA …) and secret storage | Stubs – store credentials in a vault, keep only `secret_ref` in DB |

## 6. Suggested build order

1. Run as-is, point the SPA at `/api/v1` (replace each `localStorage`/array with a call).
2. Real connectors for 2–3 sources (PubMed, ClinicalTrials.gov, Open Targets – all open APIs) + a real `LLMClient`.
3. Implement the AI Search synthesis with citations → then CIOA agents → then exports.
4. Move runs to Arq/Celery + Redis; add Alembic migrations; replace `JSON` with `JSONB` on Postgres.
5. SSO (OIDC/SAML) instead of local passwords; rate limits; per-source licence enforcement; structured logging.
