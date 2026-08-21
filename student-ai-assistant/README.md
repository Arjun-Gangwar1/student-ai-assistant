# Student AI Assistant

Deadline radar, morning digest and natural-language Q&A over a student's own
Google Classroom, Calendar, Gmail and the IIT Dharwad website.

The wedge is one question answered well — **"what's due this week?"** — delivered
where students already are, on Telegram.

---

## Run it locally

**Prerequisites:** Python 3.12, Node 20+, and either Docker or nothing at all
(the test suite starts its own embedded Postgres).

```bash
# 1. Backend
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill it in — see below

# 2. Database
docker compose up -d db redis         # or point DATABASE_URL at any Postgres
python scripts/migrate.py

# 3. Seed some data to look at (optional)
python ../scripts/seed_demo_data.py

# 4. Run
uvicorn app.main:app --reload         # http://localhost:8000/docs

# 5. Frontend, in another terminal
cd ../frontend
npm install
npm run dev                           # http://localhost:3000
```

For Telegram during local development, run the poller in a third terminal —
Telegram permits a webhook *or* polling, never both:

```bash
python scripts/telegram_dev_poll.py
```

### Required configuration

| Variable | Where to get it |
|---|---|
| `DATABASE_URL` | Local compose, or Supabase → Settings → Database → URI |
| `GOOGLE_CLIENT_ID` / `SECRET` | console.cloud.google.com → Credentials → OAuth client |
| `GROQ_API_KEY` | console.groq.com/keys |
| `TELEGRAM_BOT_TOKEN` | @BotFather → `/newbot` |
| `SECRET_KEY` | `python3 -c "import secrets;print(secrets.token_hex(32))"` |
| `TOKEN_ENCRYPTION_KEY` | `python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` |

> **Back up `TOKEN_ENCRYPTION_KEY`.** It decrypts stored Google tokens. Lose it
> and every user must reconnect their account.

---

## Tests

```bash
cd backend
python -m pytest              # 114 tests, ~90s
```

They run against a real Postgres started automatically via `pgserver` — no
Docker and no root required. Testing this layer against SQLite or mocks would
miss the point: pgvector operators, generated `tsvector` columns, `ON CONFLICT`
semantics and RRF fusion only exist in real Postgres.

---

## How it fits together

```
Classroom ─┐
Calendar  ─┼─► connectors ─► items ─► pipeline ─► classify / extract / embed
Gmail     ─┤                  │                          │
Website   ─┘                  │                          ▼
                              │                    deadlines table
                              ▼                          │
                    pgvector + tsvector           ┌──────┴──────┐
                              │                   ▼             ▼
                              ▼             alert engine   morning digest
                    hybrid search (RRF)           └──────┬──────┘
                              │                          ▼
                              ▼                    Telegram bot
                       RAG answer ◄─────────────────────┘
```

| Directory | What lives there |
|---|---|
| `backend/app/connectors/` | One module per data source; each returns counts and swallows its own failures |
| `backend/app/intelligence/` | Classification, deadline extraction, embeddings, ranking |
| `backend/app/rag/` | Retrieval and grounded answer generation |
| `backend/app/alerts/` | Digest, deadline reminders, Telegram commands |
| `backend/app/db/` | asyncpg pool and every SQL query |
| `backend/migrations/` | Ordered, idempotent SQL applied by `scripts/migrate.py` |
| `frontend/app/` | Next.js 15 PWA |

---

## Design decisions worth knowing

**Postgres directly, not PostgREST.** Supabase *is* Postgres, so one
`DATABASE_URL` covers local development and production. The earlier
supabase-py setup could not run at all without a live Supabase project — which
is how this project came to be blocked when its free-tier project was reaped
after two months of inactivity.

**Embeddings run locally.** `all-mpnet-base-v2` on the server: no per-token
cost on the one operation that scales with every ingested item.

**Model ids are configuration.** Groq retired the entire `llama-3.1` family this
project was built on, and every call started returning `404 model_not_found`.
`GROQ_MODEL` makes the next decommission an env change.

**Unconfirmed deadlines never alert.** Anything the extractor is under 0.8
confident about is stored, shown, and marked for review — but sends no reminder
until a human confirms it. A 6am alert for a hallucinated date costs more trust
than a missed reminder.

**Deadlines dedup on `(student_id, dedup_key)`.** Alert flags reset only when
`due_at` actually moves, so re-syncing does not re-fire reminders.

---

## Deployment

| Piece | Where | Notes |
|---|---|---|
| Frontend | Vercel | Set `NEXT_PUBLIC_BACKEND_URL` |
| Backend | Railway | `railway.json` present; healthcheck on `/health` |
| Database | Supabase | Point `DATABASE_URL` at it and run `scripts/migrate.py` |

In production the app refuses to start if configuration is incoherent — a
missing webhook secret, a non-HTTPS frontend URL, a short signing key. Failing at
boot beats failing at 7:30am on a student's digest.

---

## Documentation

- [`docs/SECURITY_ROTATION.md`](../docs/SECURITY_ROTATION.md) — credential rotation runbook
- [`docs/PRIVACY.md`](../docs/PRIVACY.md) — scopes, DPDP obligations, CASA
- [`PROJECT_STATUS.md`](../PROJECT_STATUS.md) — audit and remaining work
- [`MASTER_PLAN.md`](../MASTER_PLAN.md) — strategy, phases, metrics
