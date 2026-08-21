# Project Status Audit — Student Personal AI Assistant

> **Audit date:** 2026-08-21
> **Auditor:** senior engineering review, full file-by-file read
> **Supersedes:** the "Current state: Zero code" claim in `MASTER_PLAN.md` §1 (stale since 2026-06-10)

---

## 0. TL;DR

| | |
|---|---|
| **Phase 1 completion** | ~70% built, 0% operational |
| **Lines of code** | 3,762 backend (Python) + ~1,100 frontend (TS/TSX) |
| **Blocking issue** | Supabase project deleted — `nfegtbxyesezcagiiyug.supabase.co` is NXDOMAIN |
| **Live credentials** | Groq ✅ · Telegram bot ✅ · Google OAuth client ❓ · Supabase ❌ |
| **Version control** | **None.** Not a git repo. |
| **Tests** | **None.** Zero test files. |
| **Security posture** | Critical: leaked service-role key + IDOR on every data endpoint |

---

## 1. What Actually Exists

### 1.1 Backend — FastAPI (`student-ai-assistant/backend/`)

Verified: imports cleanly on Python 3.12.3, registers 22 routes, all deps installed in `venv/`.

| Layer | File | LOC | State |
|---|---|---|---|
| **Entry** | `app/main.py` | 86 | ✅ Done — lifespan, CORS, session middleware, scheduler boot |
| | `app/config.py` | 55 | ✅ Done — pydantic-settings |
| **DB** | `app/db/supabase.py` | 257 | ✅ Done — 20 helper fns (students, items, deadlines, alerts, emails) |
| **Auth** | `app/api/auth.py` | 177 | ⚠️ Works, but see §3.1 |
| **API** | `app/api/chat.py` | 36 | ⚠️ No authz |
| | `app/api/deadlines.py` | 68 | ⚠️ No authz |
| | `app/api/items.py` | 43 | ⚠️ No authz |
| | `app/api/emails.py` | 106 | ✅ Session-authed correctly |
| | `app/api/sync.py` | 65 | ✅ Session-authed correctly |
| | `app/api/telegram_webhook.py` | 117 | ⚠️ `/start` linking broken (§3.4) |
| **Connectors** | `app/connectors/classroom.py` | 143 | ✅ Courses + coursework + announcements |
| | `app/connectors/calendar_conn.py` | 125 | ✅ Events, 30d lookahead |
| | `app/connectors/gmail_conn.py` | 453 | ✅ Full — body cleanup, PDF/docx text extraction, doc-link mining |
| | `app/connectors/website.py` | 168 | ⚠️ IITdh Drupal scraper; fan-out doesn't scale (§3.6) |
| **Intelligence** | `app/intelligence/llm_client.py` | 102 | ✅ Groq + DeepInfra behind an ABC |
| | `app/intelligence/classifier.py` | 88 | ✅ Groq JSON mode, 8 categories |
| | `app/intelligence/extractor.py` | 82 | ✅ Confidence-gated at 0.8 per trust rule |
| | `app/intelligence/embedder.py` | 43 | ✅ Local `all-mpnet-base-v2`, 768-dim, zero API cost |
| | `app/intelligence/ranker.py` | 59 | ✅ relevance × priority × recency × urgency |
| | `app/intelligence/pipeline.py` | 94 | ⚠️ 2s sleep/item serially — see §3.5 |
| **RAG** | `app/rag/retriever.py` | 95 | ⚠️ "Hybrid" is semantic-only in practice (§3.3) |
| | `app/rag/generator.py` | 74 | ✅ Grounded, source-cited, Hinglish-aware |
| **Alerts** | `app/alerts/telegram_bot.py` | 75 | ✅ send/alert/digest/set_webhook |
| | `app/alerts/digest.py` | 101 | ⚠️ Ignores per-student `digest_time` |
| | `app/alerts/engine.py` | 58 | ✅ 48h/24h/6h with sent-flags |
| **Worker** | `app/workers/sync_worker.py` | 146 | ⚠️ 6 APScheduler jobs; Gmail every 3 min (§3.5) |
| **Models** | `app/models/*.py` | 98 | ✅ Pydantic schemas (mostly unused by routes) |
| **Utils** | `app/utils/date_utils.py` | 64 | ✅ IST handling, Classroom date parsing |
| **Ops** | `Dockerfile`, `railway.json`, `docker-compose.yml` | — | ✅ Deploy-ready configs |

**Dev scripts:** `run_sync.py`, `run_gmail_sync.py`, `re_embed_all.py`, `telegram_dev_poll.py` (223 LOC, richer than the webhook — has `/emails` subcommands the webhook lacks).

### 1.2 Frontend — Next.js 15 PWA (`student-ai-assistant/frontend/`)

| Page/Component | State |
|---|---|
| `app/page.tsx` (landing) | ✅ — but hardcodes `http://localhost:8000` and makes a **false privacy claim** (§3.2) |
| `app/(dashboard)/dashboard` | ✅ Stats + radar + priority inbox |
| `app/(dashboard)/deadlines` | ✅ With confirm/correct feedback loop (the moat feature) |
| `app/(dashboard)/chat` | ✅ Multi-turn, source citations, suggestion chips |
| `app/(dashboard)/settings` | ✅ Year/branch profile + Telegram instructions |
| `components/DeadlineRadar`, `PriorityInbox`, `ServiceWorkerRegistrar` | ✅ |
| `public/sw.js`, `manifest.json`, icons | ✅ Installable PWA |
| `app/(auth)/login/` | ❌ **Empty directory** |
| `app/api/auth/[...nextauth]/` | ❌ **Empty directory** — `next-auth` is a dependency but entirely unused |
| Emails UI | ❌ **Missing** — full `/api/emails` backend exists with no frontend |

### 1.3 Database migrations

Four SQL files across **two different directories** with **colliding numbers**:

```
backend/app/db/migrations/001_initial.sql          108 LOC  base schema
backend/app/db/migrations/002_vector_search.sql     45 LOC  match_items (3-arg)  ← DEAD, superseded
backend/migrations/002_emails_structure.sql        121 LOC  emails + match_items (4-arg)  ← LIVE
backend/migrations/003_student_profile.sql          11 LOC  year + branch
```

There is no single ordered migration path and no migration runner that works (`scripts/setup_db.py` splits on `;`, which breaks on the `plpgsql` function bodies).

---

## 2. Plan vs. Reality — Where You Diverged

| MASTER_PLAN said | What was actually built | Verdict |
|---|---|---|
| "Skip Gmail at launch — restricted scope, $540/yr CASA" (Rule #2: *"Never call Gmail API before Phase 2"*) | `gmail.readonly` is in the **live OAuth scope list**; 453-LOC connector; syncs **every 3 minutes** | **Deliberate override.** Needs a decision, not a silent drift — see §3.2 |
| Phase 2: website scraper | Already built (`website.py`, IITdh Drupal selectors) | ✅ Ahead of plan |
| Phase 2: PDF parsing via Gemini | Built, but via `pdfplumber`/`python-docx` locally, not Gemini. `pdf_parser.py` never created; `GEMINI_API_KEY` unset | Partial |
| `intelligence/pdf_parser.py` | ❌ Missing |
| `workers/process_worker.py` | ❌ Missing (folded into `pipeline.py` — fine) |
| `connectors/telegram.py` (group listener) | ❌ Missing |
| `backend/tests/` | ❌ Missing |
| `docs/api.md` | ❌ Missing |
| `components/ChatInterface.tsx`, `DailyDigest.tsx` | ❌ Missing (chat inlined into page — fine) |

**Roadmap position:** Weeks 1–10 substantially done. Week 11–12 (polish, error handling, rate limiting, deploy, 10 beta users) **not started**. Phase 2 partially jumped ahead of the retention gate.

---

## 3. Defects Found

### 3.1 🔴 CRITICAL — Broken authorization (IDOR) on every data endpoint

`GET /api/deadlines/{student_id}`, `GET /api/items/{student_id}`, and `POST /api/chat/ask` take `student_id` **from the URL or request body and never check the session**. `PATCH /api/items/{id}/read` and `PATCH /api/deadlines/{id}/confirm` have no auth at all.

Anyone with a student UUID can read that student's deadlines, inbox, **and run RAG queries over their private Gmail contents**. `/api/emails` and `/api/sync` do this correctly — those three don't.

### 3.2 🔴 CRITICAL — Leaked production secrets, and they're not gitignored

`backend/.env.example` contains **real, live credentials**, not placeholders:

- `SUPABASE_SERVICE_ROLE_KEY` — full DB admin, bypasses all RLS
- `GOOGLE_CLIENT_SECRET` — OAuth client takeover
- `GROQ_API_KEY` — verified live, billable
- `TELEGRAM_BOT_TOKEN` — verified live, full bot control
- `OPENAI_API_KEY`
- `SECRET_KEY` — session-cookie signing key; forge any session

`.gitignore` covers `.env` but **not `.env.example`**. The moment this becomes a repo and gets pushed, all of it is public.

**Compounding:** `students.google_tokens` stores Google refresh tokens in **plaintext JSONB** (the schema comment says "encrypted OAuth token blob" — nothing encrypts it). Leaked service-role key + plaintext refresh tokens with `gmail.readonly` = full mailbox takeover for every user.

**Also:** the landing page states *"No Gmail data collected"* while the app reads, stores, and indexes full email bodies and attachments. That is a false privacy representation — a Google OAuth policy violation and a DPDP Act problem.

### 3.3 🟠 HIGH — Hybrid search is actually semantic-only

`retriever.py:53` passes the raw user question to `.text_search("title", query)`. Postgres `to_tsquery` rejects unquoted spaces and punctuation, so *"what assignments are due this week?"* throws. It's inside a `try/except` that logs a warning and returns `[]`. The keyword half of "hybrid retrieval" has silently never worked.

### 3.4 🟠 HIGH — Deadline duplication + broken Telegram linking

- `upsert_deadline()` calls `.upsert(data)` with **no `on_conflict`**, and no `id` is supplied → Postgres inserts a **new row every sync**. Classroom/Calendar poll every 2h, so each assignment accumulates ~12 duplicate rows/day, each with fresh `alert_sent_*` flags → **duplicate 48h/24h/6h alerts and duplicate digest lines**. Directly violates Non-Negotiable Rule #1 (trust).
- `telegram_webhook.py` queries `students.telegram_link_token` — **that column exists in no migration**. The `/start <token>` account-linking flow cannot work.

### 3.5 🟡 MEDIUM — Scheduler will not scale past a handful of users

`sync_all_gmail` runs **every 3 minutes**, loops all students serially, and `process_student_items` sleeps **2s per item** for Groq rate limits. One student with 50 new emails = ~100s of pure sleep. At ~2 students the 3-minute job overruns itself; `max_instances=1` then silently drops runs. Needs a real queue (Redis/RQ is already a dependency and unused) with per-student jobs and batched embedding.

### 3.6 🟡 MEDIUM — Website scraper fan-out

`sync_website_to_all_students()` writes **one `items` row per notice per student**. 1,000 students × 50 notices = 50,000 rows and 50,000 LLM classification calls for 50 pieces of content. Needs a shared `global_items` table with per-student relevance joins.

### 3.7 Lower severity

- `app_env == "fallback"` is the DeepInfra switch — you can never run the fallback provider *in production*. Should be a distinct `LLM_PROVIDER` var (the file's own docstring says so).
- Refreshed Google access tokens are never persisted back to the DB — silent re-refresh on every call.
- `digest.py` uses deprecated `datetime.utcnow()`; per-student `digest_time` column is ignored (one global cron).
- `docker-compose.yml` mounts only `001_initial.sql` — local Postgres gets no `emails` tables and no `match_items`.
- `ivfflat` index with `lists = 10` created on an empty table; needs rebuild + `ANALYZE` after data loads.
- No rate limiting, no error monitoring (Sentry), no CI, no structured logging.
- No DPDP compliance surface: no privacy policy, no consent record, no data export, no account deletion.

---

## 4. What Remains

### ✅ Tier 0 — Unblock — **DONE 2026-08-21**
- [x] Consolidated, ordered, idempotent migrations + a runner that handles `plpgsql` bodies
- [x] `git init`, `.gitignore`, secrets scrubbed **before** the first commit, scanner wired in
- [x] Moved off PostgREST to asyncpg — local Postgres and Supabase are now one code path
- [ ] **Rotate every leaked credential** — see `docs/SECURITY_ROTATION.md` *(only you can do this)*
- [ ] **Provision a durable database** — local via `docker compose`, or a new Supabase project

### ✅ Tier 1 — Correctness & safety — **DONE**
- [x] Session-derived identity on every data endpoint; IDOR closed and regression-tested
- [x] Deadline dedup on `(student_id, dedup_key)`; alert flags reset only when `due_at` moves
- [x] `telegram_link_token` column + working `/start <token>` flow + UI to issue it
- [x] Hybrid retrieval genuinely runs both halves (`websearch_to_tsquery` + OR fallback, RRF)
- [x] `google_tokens` Fernet-encrypted at rest; refreshed tokens persisted
- [x] Truthful landing copy + a real `/privacy` notice + `docs/PRIVACY.md`
- [x] Unconfirmed deadlines never alert (was violating Non-Negotiable Rule #1)

### Tier 2 — Ship-readiness (MASTER_PLAN weeks 11–12)
- [x] Test suite — **114 tests** against real Postgres, none before
- [x] Rate limiting on chat and sync; Telegram markdown fallback; structured error handling
- [x] Emails UI page
- [x] Batched embeddings, bounded-concurrency pipeline, incremental Gmail sync
- [x] `docs/api.md` (generated from the live OpenAPI spec)
- [ ] Error monitoring (Sentry) and CI
- [ ] Deploy: Vercel + Railway + a database
- [ ] Onboard ~10 IITdh beta users; instrument the §14 metrics

### Tier 3 — Phase 2 (only after the ≥40% W4 retention gate)
- [ ] Shared `global_items` for campus-wide content (see the note in `website.py`)
- [ ] Gemini multimodal for circular and mess-menu images
- [ ] Telegram group listener
- [ ] OAuth verification + CASA assessment — **required before ~100 users** since Gmail stays
- [ ] Feedback-loop training pipeline (the moat; `extraction_feedback` is already collecting)

---

## 5. Verified Working (2026-08-21)

Run against a real Postgres and live Groq, not inspected on paper:

| Check | Result |
|---|---|
| Migrations from an empty schema | ✅ both apply, idempotent on re-run |
| Test suite | ✅ 114 passed |
| App boot | ✅ pool, 7 scheduled jobs, embedding model (768-dim), 0 errors |
| Unauthenticated access to 16 endpoints | ✅ all 401 |
| Cross-student reads by id | ✅ 404, no data |
| RAG retrieval across students | ✅ scoped, no leakage |
| Forged / tampered session cookie | ✅ rejected |
| Deleted account's valid session | ✅ 401 immediately |
| Deadline radar with priorities | ✅ correct ordering and time-remaining |
| Q&A grounding | ✅ cites sources, states time left, handles Hinglish |
| Q&A refusal | ✅ declines to invent an exam that does not exist |
| DPDP export + delete | ✅ full export; tokens destroyed at once |
