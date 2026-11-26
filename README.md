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
* **Sample data:** with `SEED_SAMPLE_DATA=true` (already set in `.env.example`) the first start fills every screen
  for `swanik@example.com`: Drug Pipeline and Competitive Insights runs (completed, in progress, paused, failed);
  conversations with a follow-up and a changed workflow; saved, shared, archived and versioned workspace items with
  notes; an assessment with overrides and scenarios; read and unread notifications; History entries spread over six
  weeks. Already set up without it? Run `python -m app.seed_samples` once. Sample runs never call the AI service, so
  they have no AI text (pipeline `summary` is null, report sections have tables but no text) and there are no sample
  AI Search runs: ask a question with the AI service configured to get real answers. Samples from earlier versions
  (placeholder text) are removed automatically.
* **Start over:** stop the server, delete `gilead.db`, start again. It is recreated with demo and sample data.
* **Get the latest API:** `git pull`, then `pip install -r requirements.txt` and restart. Your database is upgraded
  automatically on start.
* **AI content is real or absent, never made up:** answers, pipeline summaries, report text and AI Rephrase come
  from the AI Scientific Search service (see below). Without it, runs fail with a clear reason and AI Rephrase
  returns 503. `follow_ups` are the service's suggested next questions (may be empty: then hide the chips). AI scores in Scoring start as Missing (null).
* **Run progress:** prefer polling `GET /runs/{id}` (about once a second). Stop polling when `stalled` is true (an
  "In Progress" run with no progress for 15 minutes, e.g. after a server restart) and offer Resume. `GET /runs/{id}/events` (live stream)
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
                               │             scientific_search.py     (AI Scientific Search service client)
                               │             integrations.py          (data-source connector seam, scope check)
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
4. **Everything external is behind clear seams**: AI content through `services/scientific_search.py` (the AI
   Scientific Search service), data sources through `BaseConnector` (`services/integrations.py`). Nothing is
   invented when a dependency is missing: the run fails with the reason and can be retried.
5. **Admin switches are enforced server-side**: a disabled connector is excluded from every query.
6. **RBAC** by role → permission set (`core/deps.py`), plus per-item ownership/share checks in Workspace.
7. **Audit log** for admin edits, overrides, approvals, deletes (before/after JSON).

## 3. API map (prefix `/api/v1`)

```
POST /auth/login            POST /auth/refresh      POST /auth/logout
GET  /me                    GET/PATCH /me/profile
PUT  /me/context            PUT /me/preferences
GET  /notifications         POST /notifications/read-all   POST /notifications/{id}/read
POST /help/chat

GET  /home?tab=in_progress|recent_results|saved&module=&sort=&desc=
POST /home/runs/{id}        {action: duplicate|delete}

GET  /home/summary          (KPI cards + preview)        POST /workspace/items/{id}/rerun   ("Run again")

POST /prompts/rephrase      {text, module}  ("AI Rephrase")     POST /prompts/suggest (legacy)   GET /prompts/suggested
POST /runs                  {module: ai|pipeline|cioa, question, deep_research, sources, filters, more_filters,
                             workflow, therapeutic_area, indication, molecular_target, parent_run_id, notify}
GET  /runs/{id}  /result  /thread  /events(SSE)  /report (Export: render-ready report, all modules; ?thread=true = whole conversation)
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
GET  /admin/roles           PUT /admin/roles/{rid}/permissions
GET/POST /admin/groups      PATCH/DELETE /admin/groups/{gid}
```

Interactive docs: `http://localhost:8000/docs`.

Reference PDFs in `docs/` (regenerate after API or model changes; needs `pip install reportlab`):

| Document | Generator |
|---|---|
| `docs/API_Handover.xlsx` – every endpoint with screen, permission, description and a real sample request and response | `PYTHONPATH=. python docs/build_api_excel.py` (needs openpyxl) |
| `docs/API_Screen_Integration_Guide.pdf` – endpoint ↔ screen map for FE | `python docs/build_api_doc.py docs/API_Screen_Integration_Guide.pdf` |
| `docs/Database_ER_Guide.pdf` – ER diagrams, normalisation decisions, data dictionary, DB commands, PostgreSQL DDL | `PYTHONPATH=. python docs/build_er_doc.py docs/Database_ER_Guide.pdf` |
| `ER_DIAGRAM.md` – ER diagrams per domain (Mermaid) | `PYTHONPATH=. python docs/build_er_markdown.py` |
| `docs/ER_Diagram.pdf` / `.png` – the ER diagrams as PDF and image (needs Google Chrome) | `python docs/render_er_diagram.py --png` |

### Normalised schema (v0.4)

* **Roles & permissions** are tables (`roles`, `permissions`, `role_permissions`); `users.role_id` replaces the role
  text. Permission checks read the database, so an admin can change what a role may do with
  `PUT /admin/roles/{rid}/permissions` without a release.
* **Groups** are tables (`groups`, `user_groups`) instead of a JSON list on the user. `item_shares.group_id` replaces
  the free-text group name. New admin endpoints: `GET /admin/roles`, `GET/POST /admin/groups`,
  `PATCH/DELETE /admin/groups/{gid}`. `GET /users/directory` adds `group_options [{id, name}]`; sharing accepts
  `group_id` (or the group name, as before).
* **Pipeline catalogue**: `pipeline_assets` is split into `sponsors`, `modalities`, `targets`, `indications`,
  `drug_assets` and `pipeline_programs`. Pipeline API rows keep the same fields (plus `asset_id`).
* **Scoring overrides** are rows in `assessment_overrides` (with a real `user_id` foreign key) instead of JSON;
  the `overrides` field in scoring responses is unchanged.
* **No action needed for existing databases**: `app/db/upgrade.py` runs on start-up, converts older databases in
  place (keeping users, roles, groups, shares, pipeline data and overrides) and is safe to run repeatedly.
  Covered by `tests/test_upgrade.py`, which upgrades a real v0.3 schema with data.
* API responses are unchanged apart from the additions above, so existing frontend integrations keep working.

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
* **Refresh tokens**: login also returns a `refresh_token` (valid `JWT_REFRESH_EXPIRE_DAYS`, default 7). When an
  API call returns 401, `POST /auth/refresh {refresh_token}` returns a new `access_token` + `refresh_token`; each refresh
  token works once (rotation, revoked in `revoked_tokens`). Refresh tokens are rejected as Bearer tokens.
  `POST /auth/logout {refresh_token}` also revokes the refresh token. No schema change.
* **Schema**: `revoked_tokens` table; `runs.thread_id`, `runs.notify`, `runs.workflow`. On start-up `add_missing_columns()` adds them to an
  existing dev database; PostgreSQL uses Alembic migrations (see "Using PostgreSQL"). `seed_reference()` idempotently adds the 5 workflows,
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

### AI Scientific Search service

All AI-written content comes from the AI team's **AI Scientific Search** service (AWS API Gateway): AI Search
answers, Drug Pipeline summaries, Competitive Insights report text, and AI Rephrase in every module. Set its base URL in `.env` (ask the AI team; do not commit it, the service has no API key):
```bash
AI_SEARCH_BASE_URL=https://<api-id>.execute-api.ap-south-1.amazonaws.com
AI_SEARCH_TIMEOUT_SECONDS=90       # a search takes about 10-15 seconds
```
| Service endpoint | Used for |
|---|---|
| `POST /scientific-search/query` `{"query"}` | The search stage of every run (AI Search answer, Pipeline summary); for Competitive Insights also one call per report section, run 3 at a time |
| `POST /scientific-search/rephrase` `{"query"}` | AI Rephrase on every ask box (`POST /api/v1/prompts/rephrase`) |
| `GET /health` | `GET /health/dependencies` (monitoring) |

* The frontend contract does not change. The service's `[S1]` citations become `[1]`, matching `references[].number`;
  references also carry `doi`, `journal`, `publication_date`, `record_id`. Results include `provider`
  (`ai_scientific_search`; null for sample runs) and `retrieved_passages`.
* Errors and timeouts mark the run **Failed** with the service's message; **Retry** (resume) calls it again.
  Gateway errors (502/503/504) are retried once automatically; timeouts are not (to avoid paying twice).
  If AI Rephrase is unavailable the endpoint returns 503.
* Out-of-scope questions are answered locally and never sent to the service.
* Without `AI_SEARCH_BASE_URL` runs fail with "The AI Scientific Search service is not configured" and AI Rephrase
  returns 503. Sample data never calls the service; tests use a recorded reply (`tests/fixtures/ai_search_query.json`).
* Follow-up questions are sent on their own: the service does not keep conversation history.
* `follow_ups` (AI Search and Pipeline) are the service's `follow_up_questions`, without questions already asked in
  the conversation. Empty when the service suggests none.
* If a Competitive Insights report section fails, the run fails; **Retry** writes only the missing sections.
* The service cannot score indications yet (scoring questions return 500 on its side), so AI scores start as
  Missing and users enter scores as overrides. `scoring.ai_recommend_scores` is the place to connect a scoring endpoint.

### Using PostgreSQL

SQLite (`gilead.db`) is the default for local development. Shared environments use PostgreSQL.

| | SQLite (default) | PostgreSQL |
|---|---|---|
| Schema on start-up | `app/db/upgrade.py` (automatic, converts older databases) | Alembic migrations in `migrations/versions/` |
| JSON columns | JSON | JSONB |
| Text length limits | not enforced | enforced (inputs are validated to fit) |

**Start a local PostgreSQL** (or use your own server):
```bash
docker compose up -d db        # PostgreSQL 16 on localhost:5432, user/password/db: gilead (local use only)
```

**Move existing SQLite data into it** (the SQLite file is never changed):
```bash
python -m app.db.sqlite_to_postgres --target postgresql+psycopg://gilead:gilead@localhost:5432/gilead --check
python -m app.db.sqlite_to_postgres --target postgresql+psycopg://gilead:gilead@localhost:5432/gilead
```
It upgrades a temporary copy of the SQLite file to the current schema, checks the data fits PostgreSQL's column
limits, applies the migrations, copies all tables in one transaction (all or nothing), resets id sequences and compares
every row. The target must be empty; `--replace` empties it first (deletes its data).

**Point the API at it** in `.env`, then start as usual (migrations run on start-up):
```bash
DATABASE_URL=postgresql+psycopg://gilead:gilead@localhost:5432/gilead
SEED_SAMPLE_DATA=false          # shared environments: no sample activity
```
Starting the API on an empty PostgreSQL database creates the schema and reference data (roles, permissions,
indications) by itself; the copy step is only needed to keep existing data.

**Schema changes** (PostgreSQL): edit `app/db/models.py`, then
```bash
alembic revision --autogenerate -m "describe the change"   # review the generated file in migrations/versions/
alembic upgrade head                                        # or just restart the API
alembic check                                               # fails if models changed without a migration
```

**Tests on PostgreSQL**: `TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/gilead_test pytest`
(uses an empty test database and drops its contents; the SQLite-to-PostgreSQL tests run only in this mode).

### Deploying to AWS Lambda

The backend is ready to run on AWS Lambda behind API Gateway. Packaging and infrastructure (zip or container image,
API Gateway, IAM, networking) are owned by DevOps; this section is what they need from the backend.

**Function**

| Item | Value |
|---|---|
| Handler | `app.lambda_handler.handler` (FastAPI through **Mangum**) |
| Runtime | Python 3.12 |
| Dependencies | `requirements-lambda.txt` (install for Linux: `pip install -r requirements-lambda.txt -t build/ --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.12`; use `aarch64` for Graviton) |
| Code to package | `app/`, `migrations/`, `alembic.ini` |
| Timeout | 15 minutes (900 s): the same function executes research runs, which take 15-60 s |
| Memory | 1024 MB recommended |
| Trigger | API Gateway HTTP API (payload 2.0) or REST API, proxy route `ANY /{proxy+}`; serve from the root of the stage (or a custom domain) so `/docs` and `/api/v1/...` resolve |
| Async invocation | Set retry attempts to **0** (a failed run is shown as Failed and retried by the user, never billed twice silently) |

**How research runs work on Lambda**: API Gateway ends requests after about 30 seconds and a run takes 15-60
seconds. So `POST /runs` saves the run, invokes the same function asynchronously with
`{"task": "execute_run", "run_id": N}` and returns 202 at once; the frontend polls `GET /runs/{id}`. Live streaming
(`/runs/{id}/events`) is not available through API Gateway (it sends one snapshot and closes). To run the runs in a
separate function, set `WORKER_FUNCTION_NAME`.

**Secrets (AWS Secrets Manager)**

| Secret | Content | Environment variable with its ARN |
|---|---|---|
| Application secret | JSON, keys = setting names: `{"JWT_SECRET": "<openssl rand -hex 32>", "AI_SEARCH_BASE_URL": "https://...", "AI_SEARCH_API_KEY": ""}` | `APP_SECRET_ARN` |
| Database credentials | The secret RDS creates with **Manage master credentials in AWS Secrets Manager** (`username`, `password`), or the standard RDS format (`host`, `port`, `dbname`, `username`, `password`) | `DB_SECRET_ARN` |

* Secrets are read at cold start and cached for 5 minutes per container. Any setting can also be a plain
  environment variable; an explicit environment variable wins over the secret.
* The database password is never put in a URL or environment variable: it is added when each connection opens.
  When RDS rotates it, the first rejected connection re-reads the secret, so **no redeploy is needed**.

**Amazon RDS (PostgreSQL 16 recommended)**

| Environment variable | Value |
|---|---|
| `DB_SECRET_ARN` | ARN of the database credentials secret |
| `DB_HOST` | RDS endpoint (or RDS Proxy endpoint), e.g. `gilead.xxxx.ap-south-1.rds.amazonaws.com`; not needed if the secret has `host` |
| `DB_NAME` | Database name, e.g. `gilead` (create it once); not needed if the secret has `dbname` |
| `DB_PORT` | Optional, default 5432 |
| `DB_SSLMODE` | Optional, default `require` (connections are encrypted) |

Alternatively set `DATABASE_URL=postgresql+psycopg://user:password@host:5432/gilead` directly (no rotation support).
Tables are created and upgraded automatically (Alembic) on cold start; concurrent cold starts are safe (advisory
lock). SQLite is refused on Lambda. An **RDS Proxy** is recommended: busy Lambdas open many connections (each
container keeps at most 4).

**Other environment variables**

| Variable | Value |
|---|---|
| `APP_ENV` | `prod` |
| `CORS_ORIGINS` | Frontend URL(s), comma-separated |
| `SEED_DEMO_DATA` | `true` creates demo users and reference data (needed once for login in test environments) |
| `SEED_SAMPLE_DATA` | `false` in production (`true` only for frontend test environments) |
| `AI_SEARCH_TIMEOUT_SECONDS` | Optional, default 90 |

**IAM permissions for the function role**
* `secretsmanager:GetSecretValue` on both secrets (plus `kms:Decrypt` if they use a customer-managed KMS key)
* `lambda:InvokeFunction` on the function itself (or on `WORKER_FUNCTION_NAME`), to start research runs
* `AWSLambdaVPCAccessExecutionRole` when the function runs in a VPC; CloudWatch Logs as usual

**Network**: with RDS in a private VPC, the function runs in private subnets and its security group must be
allowed into the database on 5432. From the VPC it also needs to reach the **AI service** (public API Gateway:
NAT gateway), **Secrets Manager** and the **Lambda API** (NAT gateway, or VPC interface endpoints for
`secretsmanager` and `lambda`).

**After each deployment** (optional, creates or upgrades tables without API Gateway's time limit):
```bash
aws lambda invoke --function-name <function> --payload '{"task": "init"}' --cli-binary-format raw-in-base64-out out.json
```
Then open `<API URL>/health/dependencies` (AI service status) and `<API URL>/docs`.

## 5. What is real and what is still missing (be aware before going live)

| Area | State |
|---|---|
| Scoring, pipeline aggregations, weights validation, overrides, scenarios, sensitivity | Implemented + unit-tested |
| Auth (JWT + logout revocation), profile, RBAC, audit, workspace sharing/versions, history, admin CRUD, connector toggle | Implemented |
| Run engine (steps, cancel/pause/resume, notifications, SSE) | Implemented |
| AI Search answers, Pipeline summaries, Competitive Insights report text, AI Rephrase | **Real** via the AI Scientific Search service (`AI_SEARCH_BASE_URL`); fails clearly without it |
| Pipeline tables, charts and report statistics | **Real** database content (`pipeline_programs` and lookups) |
| Scope guardrail | Keyword check (`integrations.in_scope`) so off-topic questions are not sent to the billed service |
| **AI scores** | **Missing** until the AI service can score (`scoring.ai_recommend_scores`) |
| Follow-up suggestions | **Real**: the service's `follow_up_questions` (AI Search and Pipeline answers) |
| **Connector clients (PubMed, Citeline, IQVIA …) and secret storage** | **Not connected**: Test reports "No client is connected"; register a `BaseConnector` per source, keep credentials in a vault and only `secret_ref` in the DB |
| Exports (PDF) | `/runs/{id}/report` returns render-ready report JSON for AI Search, Pipeline and Competitive Insights (same shape); PDF rendering is client-side as in the prototype |

## 6. Suggested build order

1. Run as-is, point the SPA at `/api/v1` (replace each `localStorage`/array with a call).
2. Real connectors for 2–3 sources (PubMed, ClinicalTrials.gov, Open Targets – all open APIs).
3. A scoring endpoint from the AI team for AI scores → then exports.
4. Move runs to Arq/Celery + Redis. (Alembic migrations and JSONB on PostgreSQL are in place.)
5. SSO (OIDC/SAML) instead of local passwords; rate limits; per-source licence enforcement; structured logging.
