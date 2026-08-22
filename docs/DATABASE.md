# Database setup

The app speaks plain PostgreSQL. Local Postgres, Docker, Supabase and any managed
provider are all the same code path — **only `DATABASE_URL` changes**. That is deliberate:
the previous design could not run at all without a live Supabase project, which is exactly
how this project became blocked when its free-tier project was reaped after two months idle.

---

## Which one should you use?

| | Local (`dev_db.py`) | Docker | Supabase |
|---|---|---|---|
| Setup effort | none | needs group + re-login | ~5 min, browser |
| Works offline | ✅ | ✅ | ❌ |
| Survives inactivity | ✅ | ✅ | ⚠️ **pauses after 7 days idle** |
| Reachable from the internet | ❌ | ❌ | ✅ |
| Right for | daily development | matching prod locally | **deployment** |

**Recommendation: use local for development, Supabase for deployment.** Keeping the daily
loop local means an idle week never breaks your dev environment again.

---

## Option A — local, zero setup (already working)

```bash
cd student-ai-assistant/backend
./venv/bin/python scripts/dev_db.py start     # start + migrate, updates .env
./venv/bin/python scripts/dev_db.py status
./venv/bin/python scripts/dev_db.py stop
./venv/bin/python scripts/dev_db.py reset     # wipe and rebuild
```

Real PostgreSQL 16 with pgvector, via the `pgserver` package — no Docker, no root. Data lives
in `~/.local/share/studentai-devdb`, outside the repo.

---

## Option B — Docker

Two prerequisites, both one-time:

```bash
sudo usermod -aG docker $USER          # you have already run this
sudo apt install docker-compose-v2     # NOT docker-compose-plugin, which is Docker's
                                       # own apt repo's name for the same thing
```

Then **log out and back in** — Linux group membership is applied at login, so your current
shell still lacks it. Verify with `id -nG | grep docker` in a *new* session, or test with
`docker ps` (no sudo).

```bash
cd student-ai-assistant
docker compose up -d db redis
cd backend && ./venv/bin/python scripts/migrate.py
```

`DATABASE_URL` for this is `postgresql://studentai:studentai@localhost:5432/studentai`.

---

## Option C — Supabase (for deployment)

### 1. Organisation

Name it anything, **Type: Personal**, **Plan: Free**. Free is genuinely enough here: 500MB
database and 5GB bandwidth is far more than a campus pilot needs.

### 2. Create the project

| Field | Value | Why |
|---|---|---|
| Name | `student-ai-assistant` | |
| Database Password | **Generate a strong one and save it immediately** | Shown once. It becomes part of your connection string. |
| Region | **South Asia (Mumbai)**, or Southeast Asia (Singapore) | Every query crosses this distance. A US region adds ~250ms per round trip. |

Put the database password in your password manager next to `TOKEN_ENCRYPTION_KEY`. It is not
recoverable — only resettable, which invalidates your connection string.

Provisioning takes about two minutes.

### 3. Get the connection string

**Project Settings → Database → Connection string**, and you will see three options. **This
choice matters and is the most common thing to get wrong:**

| Mode | Port | Use it when |
|---|---|---|
| **Direct connection** | 5432 | You have IPv6. On the free tier this is **IPv6-only** — on an IPv4-only network it simply times out. |
| **Session pooler** | 5432 | **Safest default.** IPv4-compatible, behaves like a normal Postgres connection. |
| **Transaction pooler** | 6543 | Serverless / many short-lived connections. |

**Start with Session pooler.** If direct works on your network, it is marginally faster, but
session pooler avoids the confusing "connection timed out" that IPv6-only causes.

> The app already sets `statement_cache_size=0` in `app/db/pool.py`, which is what makes
> pgbouncer-backed pooler connections work. No extra configuration needed.

### 4. Put it in `.env`

The copied string contains a literal `[YOUR-PASSWORD]` placeholder — replace it:

```bash
DATABASE_URL=postgresql://postgres.abcdefgh:YOUR_REAL_PASSWORD@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
```

> **If your password contains `@ : / ? # [ ] %`**, it must be percent-encoded or it will
> corrupt the URL. Generate one and encode it in a single step:
>
> ```bash
> python3 -c "import secrets,urllib.parse; p=secrets.token_urlsafe(24); print('password:',p); print('url-safe:',urllib.parse.quote(p,safe=''))"
> ```

### 5. Migrate and verify

```bash
cd student-ai-assistant/backend
./venv/bin/python scripts/migrate.py --status    # connectivity check, applies nothing
./venv/bin/python scripts/migrate.py             # build the schema
```

`--status` first is worth the extra step: it proves the connection string works before any
DDL runs, so a typo shows up as a clear connection error rather than a half-built schema.

---

## Switching between them

`DATABASE_URL` is the only thing that changes. `dev_db.py start` preserves whatever was there
as a `# previous:` comment, so going back is a copy-paste.

```bash
# check which database you are pointed at right now
grep '^DATABASE_URL=' .env
./venv/bin/python scripts/migrate.py --status
```

Migrations are idempotent and tracked in `schema_migrations`, so running them against a fresh
database is always safe.

---

## The free-tier pause — read this one

**Supabase free-tier projects pause after 7 days with no activity, and are eventually
deleted.** This is not a footnote; it is what destroyed this project's first database and cost
two months of progress.

Mitigations, in order of sensibility:

1. **Do daily development locally.** An idle Supabase project then does not matter.
2. **Once deployed, real traffic keeps it alive** — the digest job alone touches it daily.
3. **Before a long break, take a backup:**
   ```bash
   pg_dump "$DATABASE_URL" > backup-$(date +%F).sql
   ```
4. **Upgrade to Pro ($25/mo) only when you have users.** Paid projects never pause.

If a project does get paused, the dashboard offers to restore it. If it was deleted, the
schema rebuilds from `scripts/migrate.py` in seconds — but the *data* is gone, which is why
backups matter once you have real students using it.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Connection refused` on localhost | Nothing is running — `dev_db.py start` or `docker compose up -d db` |
| Connection times out on Supabase | Direct connection on an IPv4-only network → use Session pooler |
| `password authentication failed` | Unencoded special character in the password, or `[YOUR-PASSWORD]` left literal |
| `Tenant or user not found` | Pooler username must be `postgres.<project-ref>`, not plain `postgres` |
| `prepared statement already exists` | Pooler without `statement_cache_size=0` — already handled in `app/db/pool.py` |
| Migrations apply, but tables look empty | You are connected to a *different* database than the app; check `grep DATABASE_URL .env` |
