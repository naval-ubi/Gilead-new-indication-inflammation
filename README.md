# Inflammation – New Indication Platform · FastAPI backend

Backend for the prototype (originally `Mon_16_Nov_New_Indication.html`, updated for `Updated Design Indication.html`). The prototype keeps all state in
JavaScript arrays and `localStorage`; this project replaces each of those with a real API.

## Getting started for frontend developers

You run your own copy of the API on your machine. No shared database is needed: on first start the API creates
`gilead.db` (SQLite) and fills it with demo data (users, data sources, pipeline assets, scoring model, workflows).

**1. Install and start** (Python 3.10 or newer; 3.12+ recommended)
```bash
git clone https://github.com/navalpatil-champprog/Gilead-new-indication-inflammation.git
cd Gilead-new-indication-inflammation
python3 -m venv .venv
source .venv/bin/activate                # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                     # Windows: copy .env.example .env
uvicorn app.main:app --reload
```

**2. Use it**

| What | Where |
|---|---|
| API base URL | `http://localhost:8000/api/v1` |
| Try every endpoint in the browser | `http://localhost:8000/docs` (click *Authorize* and paste the token from `/auth/login`) |
| OpenAPI schema (for generating types / clients) | `http://localhost:8000/openapi.json` |
| Which endpoint each screen calls | [`docs/API_Screen_Integration_Guide.pdf`](docs/API_Screen_Integration_Guide.pdf) |
| Data model | [`docs/Database_ER_Guide.pdf`](docs/Database_ER_Guide.pdf) |

Demo logins, password `ChangeMe123!`: `swanik@example.com` (Analyst), `maria.chen@example.com` (Admin, needed for
Admin and connector settings), `sam.patel@example.com` (Reviewer).

**3. Good to know**

* **CORS:** `http://localhost:5173` and `http://localhost:3000` are allowed. If your dev server uses another port,
  add it to `CORS_ORIGINS` in `.env` (comma-separated) and restart.
* **Empty screens at first:** Home, History and Workspace fill up as you use the app. To create sample data quickly,
  start a few research runs (they complete in about a second locally):
  ```bash
  TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' \
    -d '{"email":"swanik@example.com","password":"ChangeMe123!"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
  curl -s -X POST localhost:8000/api/v1/runs -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"module":"ai","question":"What role does IRAK4 play in SLE?"}'
  curl -s -X POST localhost:8000/api/v1/runs -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"module":"cioa","question":"Map IRAK4 competitors in SLE","workflow":"competitive"}'
  ```
* **Start over:** stop the server, delete `gilead.db`, start again. It is recreated with demo data.
* **Get the latest API:** `git pull`, then `pip install -r requirements.txt` and restart. Your database is upgraded
  automatically on start.
* **Placeholder content:** research answers, report text and AI Rephrase return placeholder text until the real AI
  and data sources are connected. Build against the response *structure* (field names and types), not the wording.
* **Run progress:** prefer polling `GET /runs/{id}` (about once a second). `GET /runs/{id}/events` (live stream)
  works locally but may not be available in every cloud deployment.
* **Auth:** send `Authorization: Bearer <token>` on every call. A 401 means the token expired or the user logged out:
  send them back to sign-in.

## 1. What the prototype tells us the backend must do

| UI module | What it really needs from the server |
|---|---|
| **Home** | Hero ask-box, three KPI cards (In Progress / Completed / Saved Searches) with counts, card lists with progress %, Resume / View / **Run again** |
| **AI Scientific Search** | Conversational research: staged loader (sub-items, per-source status, live activity, emerging findings), **Notify me**, **AI Rephrase**, follow-up questions, out-of-scope reply, references + "View Details" |
| **Drug Pipeline Explorer** | Conversational too: each answer has its own filter bar + 5 views (table, swimlane, graph, heatmap, radar), KPI tiles, grouped filters, **More filters** dialog, AI summary |
| **Competitive Insights** (module key `cioa`) | Ask + Therapeutic Area / Indication / Molecular Target, choose 1 of 5 **workflows**, report (stats, takeaways, sections), **Change workflow**, full-report PDF export |
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
POST /auth/login            POST /auth/logout       GET /me   GET/PATCH /me/profile
PUT  /me/context            PUT /me/preferences
GET  /notifications         POST /notifications/read-all   POST /notifications/{id}/read
POST /help/chat

GET  /home?tab=in_progress|recent_results|saved&module=&sort=&desc=
POST /home/runs/{id}        {action: duplicate|delete}

GET  /home/summary          (KPI cards + preview)        POST /workspace/items/{id}/rerun   ("Run again")

POST /prompts/rephrase      {text, module}  ("AI Rephrase")     POST /prompts/suggest (legacy)   GET /prompts/suggested
POST /runs                  {module: ai|pipeline|cioa, question, deep_research, sources, filters, more_filters,
                             workflow, therapeutic_area, indication, molecular_target, parent_run_id, notify}
GET  /runs/{id}  /result  /thread  /events(SSE)  /report (Competitive Insights full report)
POST /runs/{id}/cancel|pause|resume|rerun|save|approve|notify|change-workflow
GET  /pipeline/options      GET /pipeline/assets?target=&indication=&modality=&stage=&status=&phase=&view=
POST /pipeline/assets/query {filters, more_filters, view, sort, desc}
GET  /cioa/options          GET /cioa/workflows?question=   (5 workflows + recommended)   GET /cioa/capabilities (deprecated)

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

Reference PDFs in `docs/` (regenerate after API or model changes; needs `pip install reportlab`):

| Document | Generator |
|---|---|
| `docs/API_Screen_Integration_Guide.pdf` – endpoint ↔ screen map for FE | `python docs/build_api_doc.py docs/API_Screen_Integration_Guide.pdf` |
| `docs/Database_ER_Guide.pdf` – ER diagram, data dictionary, DB commands, PostgreSQL DDL (read from the models) | `PYTHONPATH=. python docs/build_er_doc.py docs/Database_ER_Guide.pdf` |

### Changes for the updated design (v0.2)

* **Run payload** (`GET /runs/{id}`) now has `steps[].items[]` (sub-lines of each stage; one item per data source in
  the search stage, with `active/done/failed` status), `current_activity`, `activity` (live feed), `emerging_findings`
  (preliminary, labelled Clinical / Preclinical / Genetic / Mechanistic evidence), `elapsed_seconds`, `thread_id`,
  `notify`, `workflow`, `module_name`. `progress_pct` now counts sub-items.
* **Results** gain `relevant`, `answer`, `top_sources [{name,url}]`, `references`, `follow_ups`, `research_summary`
  (stages, sources searched, elapsed). Out-of-scope questions finish quickly with `relevant: false`, a message and
  suggested questions; the remaining stages are `skipped`. Pipeline results add `summary`, `kpi_tiles`, `more_filters_count`.
  Competitive Insights results are `{workflow, context, stats, key_takeaways, sections[{title, bullets, table}], data_sources}`.
* **Threads**: pass `parent_run_id` on a follow-up; `GET /runs/{id}/thread` returns every turn.
* **Notify me**: `notify` on create or `POST /runs/{id}/notify`; it overrides an "in-app notifications off" preference.
* **Stages**: `Discovery` replaces `Research`; filters accept `Approved` (= Marketed), `Regulatory review` (= Filed),
  `On hold` and `Discontinued` (matched on trial activity).
* **Reference data** moved to `app/services/catalog.py` (workflows, filter groups, help content, module names) and is
  served by `/cioa/options`, `/cioa/workflows`, `/pipeline/options`, `/context/options`, `/help/content`.
  Module display names are now "AI Scientific Search" and "Competitive Insights".
* **Profile & logout**: `GET /me/profile` (My Profile: role, groups, permissions, active session, last login),
  `PATCH /me/profile {name}`, `POST /auth/logout` (204). Tokens now carry a `jti`; logout stores it in
  `revoked_tokens` and every authenticated call rejects it with 401. Other sessions of the same user stay signed in.
  `/me` and the login response now include `permissions`.
* **Schema**: `revoked_tokens` table; `runs.thread_id`, `runs.notify`, `runs.workflow`. On start-up `add_missing_columns()` adds them to an
  existing dev database; use Alembic for real deployments. `seed_reference()` idempotently adds the 5 workflows,
  5 report templates and prompt metadata.

Workspace item search (`q`) is case-insensitive and matches the item title,
question, type, or owner's name.

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
| Auth (JWT + logout revocation), profile, RBAC, audit, workspace sharing/versions, history, admin CRUD, connector toggle | Implemented |
| Run engine (steps, cancel/pause/resume, notifications, SSE) | Implemented |
| **LLM answers, evidence retrieval, Competitive Insights section content, AI scores, exports (PDF/PPT/Excel)** | **Stubs** – wire `integrations.py`, `orchestrator.build_*`, `scoring.ai_recommend_scores`. `/runs/{id}/report` returns render-ready report JSON; PDF rendering is still client-side as in the prototype |
| AI Rephrase, scope guardrail, follow-up suggestions | Stubs over curated questions (`integrations.rephrase_prompt`, `in_scope`, `suggest_follow_ups`) – replace with LLM calls |
| Connector clients (PubMed, Citeline, IQVIA …) and secret storage | Stubs – store credentials in a vault, keep only `secret_ref` in DB |

## 6. Suggested build order

1. Run as-is, point the SPA at `/api/v1` (replace each `localStorage`/array with a call).
2. Real connectors for 2–3 sources (PubMed, ClinicalTrials.gov, Open Targets – all open APIs) + a real `LLMClient`.
3. Implement the AI Search synthesis with citations → then CIOA agents → then exports.
4. Move runs to Arq/Celery + Redis; add Alembic migrations; replace `JSON` with `JSONB` on Postgres.
5. SSO (OIDC/SAML) instead of local passwords; rate limits; per-source licence enforcement; structured logging.
