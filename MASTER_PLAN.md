# Student Personal AI Assistant — Master Engineering Plan

> **Author:** Senior Engineering Analysis  
> **Date:** 2026-06-10  
> **Project:** Always-on, real-time student life manager for IIT Dharwad students  
> **Status:** Planning complete — ready to build

---

## 1. What Exists Right Now

| File | Content |
|------|---------|
| `1.txt` | Hackathon proposal (Pathway/Singularity) — full architecture diagrams |
| `2.txt` | Gmail classifier concept, 6-step processing pipeline, folder sketch |
| `3.txt` | 55-feature list across 6 domains (A–F), use cases |
| `4.txt` | "Parsec Singularity" resume framing, Groq + Gemini hybrid LLM notes |
| `5.txt` | Pathway use cases, ETA prediction side ideas |
| `6.txt` | Google Classroom API OAuth walkthrough |
| `7.txt` | Origin story — Gmail → AI pipeline concept |
| `research.md` | Deep India-first strategic analysis (most valuable doc) |
| `StudentAI_Complete_Project_Guide.docx` | Full guide doc |

**Current state: Zero code. 100% planning documents.** Everything below is the build plan.

---

## 2. Brutal Strategic Decisions (From `research.md` — Do Not Ignore)

| Decision | Why |
|----------|-----|
| **Skip Pathway** | Overkill for daily-changing student data. Simple webhooks + cron + pgvector is sufficient and costs nothing extra |
| **Skip Gmail at launch** | `gmail.readonly` is a *restricted* scope → mandatory CASA security audit ($540/yr). Start with Classroom + Calendar which are only *sensitive* |
| **Skip WhatsApp Business API at launch** | Costs money and requires Meta approval. Use **Telegram Bot** instead (free, instant, no approval needed, popular in Indian college groups) |
| **<100 users = no OAuth verification needed** | Run "In Production" unverified. Only verify when approaching 100 total lifetime users |
| **One wedge feature only** | `"What's due this week?"` — deadline radar from Classroom + Calendar. Everything else is Phase 2+ |
| **Moat is not tech, it is depth** | Per-college circular parsing + correction feedback loop. That's what Google won't build |

---

## 3. Final Tech Stack

```
Frontend          Next.js 15 (App Router) — PWA installable, mobile-first
Auth              Google OAuth 2.0 via NextAuth.js (sensitive scopes only)
Backend           FastAPI (Python 3.12) — async, fast, clean
Database          Supabase (Postgres + pgvector) — DB + auth + storage + edge functions
Vector Search     pgvector extension (built into Supabase, no separate Pinecone/Chroma)
LLM Text          Groq — Llama-3.1-8B-Instant ($0.05/M tokens, <1s latency)
LLM Multimodal    Google Gemini 2.0 Flash-Lite (PDFs, images, circulars)
LLM Fallback      Together.ai / DeepInfra (same Llama model, ~$0.02–0.03/M)
Notifications     Telegram Bot API (free, zero friction, India-popular)
Task Queue        Redis (Upstash free tier) + python-rq for async jobs
Scheduler         APScheduler (in-process cron) or GitHub Actions (nightly batch)
Deployment        Vercel (frontend) + Railway (backend + worker) + Supabase (DB)
Hosting Credits   Microsoft Founders Hub ($1K+), Cloudflare for Startups, Google for Startups
```

---

## 4. System Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    DATA SOURCES                          │
│  Google Classroom API  │  Google Calendar API            │
│  (sensitive scope)     │  (sensitive scope)              │
│                        │                                 │
│  College Website       │  Telegram Groups                │
│  (HTTP scraper)        │  (Bot listener)                 │
│                        │                                 │
│  ─ ─ ─ PHASE 2 ─ ─ ─  │  ─ ─ ─ PHASE 2 ─ ─ ─           │
│  Gmail API (restricted)│  Google Drive                   │
└──────────────┬───────────────────────┬───────────────────┘
               │                       │
               ▼                       ▼
┌──────────────────────────────────────────────────────────┐
│                  INGESTION LAYER                         │
│  • Classroom: poll every 2h (assignments, announcements) │
│  • Calendar:  poll every 2h (events, deadlines)          │
│  • Website:   cron scrape every 6h (circulars, notices)  │
│  • Telegram:  webhook listener (real-time messages)      │
│  All → normalize to unified Event schema → Postgres      │
└───────────────────────────┬──────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────┐
│              STREAMING INTELLIGENCE LAYER                │
│  For each new item:                                      │
│  1. Classification  → Groq LLM (JSON mode)               │
│     category: academic|admin|event|transport|general     │
│  2. Entity Extraction → deadline, event_date, action     │
│  3. Relevance Scoring → student profile match (0–1)      │
│  4. Priority Score  → HIGH / MEDIUM / LOW                │
│  5. Embed → text-embedding-3-small → pgvector            │
│  All results stored back in Postgres                     │
└───────────────────────────┬──────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────┐
│              KNOWLEDGE LAYER (Supabase + pgvector)       │
│  Tables: events, deadlines, emails(p2), student_profile  │
│         embeddings (vector), alert_history, feedback     │
│  RAG: hybrid search = keyword (Postgres FTS) +           │
│       semantic (pgvector cosine) → re-rank → LLM         │
└──────────────┬────────────────────────┬──────────────────┘
               │                        │
               ▼                        ▼
┌───────────────────────┐   ┌────────────────────────────┐
│   RAG Q&A ENGINE      │   │   ALERT & ACTION ENGINE    │
│   User asks in NL     │   │   • Daily morning digest   │
│   → hybrid retrieval  │   │   • Deadline D-48h alert   │
│   → Groq LLM answer   │   │   • Overload warning (P3)  │
│   → source-cited resp │   │   → Telegram push          │
└──────────┬────────────┘   └────────────┬───────────────┘
           │                             │
           ▼                             ▼
┌──────────────────────────────────────────────────────────┐
│                  STUDENT INTERFACE                       │
│  PWA (Next.js) — Chat UI + Dashboard + Deadline Radar    │
│  Telegram Bot — Daily digest + Q&A + Alerts              │
└──────────────────────────────────────────────────────────┘
```

---

## 5. Database Schema

```sql
-- Student profile
CREATE TABLE students (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  google_id       TEXT UNIQUE NOT NULL,
  email           TEXT NOT NULL,
  name            TEXT,
  year            INT,          -- 1, 2, 3, 4
  branch          TEXT,         -- CS, EE, ME ...
  telegram_chat_id BIGINT,
  digest_time     TIME DEFAULT '07:30',
  created_at      TIMESTAMPTZ DEFAULT now()
);

-- Unified event/item from any source
CREATE TABLE items (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id      UUID REFERENCES students(id),
  source          TEXT NOT NULL,       -- classroom|calendar|gmail|website|telegram
  source_id       TEXT,                -- original ID from source (for dedup)
  raw_content     TEXT,
  category        TEXT,                -- academic|admin|event|transport|mess|general
  title           TEXT,
  summary         TEXT,
  deadline        TIMESTAMPTZ,
  priority        TEXT DEFAULT 'LOW',  -- HIGH|MEDIUM|LOW
  relevance_score FLOAT,
  is_read         BOOL DEFAULT false,
  is_actioned     BOOL DEFAULT false,
  embedding       vector(1536),
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Extracted deadlines for the radar
CREATE TABLE deadlines (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id      UUID REFERENCES students(id),
  item_id         UUID REFERENCES items(id),
  title           TEXT NOT NULL,
  due_at          TIMESTAMPTZ NOT NULL,
  source          TEXT,
  confirmed       BOOL DEFAULT false,  -- user confirmed this is correct
  calendar_event_id TEXT,              -- if synced to Google Calendar
  alert_sent_48h  BOOL DEFAULT false,
  alert_sent_24h  BOOL DEFAULT false,
  alert_sent_6h   BOOL DEFAULT false
);

-- Alert history to prevent spam
CREATE TABLE alerts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id      UUID REFERENCES students(id),
  deadline_id     UUID REFERENCES deadlines(id),
  channel         TEXT,          -- telegram|email|push
  sent_at         TIMESTAMPTZ DEFAULT now(),
  alert_type      TEXT           -- digest|deadline_48h|deadline_24h|overload
);

-- User feedback on extracted deadlines (moat builder)
CREATE TABLE extraction_feedback (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  item_id             UUID REFERENCES items(id),
  was_correct         BOOL,
  corrected_deadline  TIMESTAMPTZ,
  created_at          TIMESTAMPTZ DEFAULT now()
);
```

---

## 6. Project Folder Structure

```
student-ai-assistant/
│
├── frontend/                        # Next.js 15 PWA
│   ├── app/
│   │   ├── (auth)/
│   │   │   └── login/page.tsx
│   │   ├── (dashboard)/
│   │   │   ├── page.tsx             # Main dashboard
│   │   │   ├── deadlines/page.tsx   # Deadline radar
│   │   │   ├── chat/page.tsx        # Q&A interface
│   │   │   └── settings/page.tsx
│   │   └── api/
│   │       ├── auth/[...nextauth]/route.ts
│   │       └── telegram/webhook/route.ts
│   ├── components/
│   │   ├── DeadlineRadar.tsx
│   │   ├── ChatInterface.tsx
│   │   ├── DailyDigest.tsx
│   │   └── PriorityInbox.tsx
│   └── lib/
│       ├── auth.ts
│       └── api-client.ts
│
├── backend/                         # FastAPI Python
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── chat.py              # Q&A endpoint
│   │   │   ├── deadlines.py
│   │   │   ├── items.py
│   │   │   └── telegram.py
│   │   ├── connectors/
│   │   │   ├── classroom.py         # Google Classroom API
│   │   │   ├── calendar.py          # Google Calendar API
│   │   │   ├── gmail.py             # Phase 2 - restricted scope
│   │   │   ├── website.py           # College portal scraper
│   │   │   └── telegram.py          # Telegram group listener
│   │   ├── intelligence/
│   │   │   ├── classifier.py        # Groq JSON-mode classification
│   │   │   ├── extractor.py         # Deadline/entity extraction
│   │   │   ├── embedder.py          # text-embedding-3-small
│   │   │   ├── ranker.py            # Relevance scoring
│   │   │   └── pdf_parser.py        # Gemini multimodal (Phase 2)
│   │   ├── rag/
│   │   │   ├── retriever.py         # Hybrid keyword + vector search
│   │   │   └── generator.py         # Groq LLM answer generation
│   │   ├── alerts/
│   │   │   ├── engine.py            # Deadline alert scheduler
│   │   │   ├── digest.py            # Daily digest builder
│   │   │   └── telegram_bot.py      # Telegram notification sender
│   │   ├── workers/
│   │   │   ├── sync_worker.py       # Polling jobs (APScheduler)
│   │   │   └── process_worker.py    # Intelligence pipeline worker
│   │   ├── models/
│   │   │   ├── student.py
│   │   │   ├── item.py
│   │   │   └── deadline.py
│   │   └── db/
│   │       ├── supabase.py
│   │       └── migrations/
│   │
│   ├── tests/
│   ├── .env.example
│   └── requirements.txt
│
├── scripts/
│   ├── seed_demo_data.py            # Demo data for testing
│   └── setup_db.py
│
├── docs/
│   └── api.md
│
└── docker-compose.yml               # Local dev: Postgres + Redis
```

---

## 7. Feature Priority Matrix

### Phase 1 — MVP (Weeks 1–12, <100 users, no OAuth verification needed)

| # | Feature | Source | Complexity | Impact |
|---|---------|--------|------------|--------|
| 1 | Google Classroom deadline fetch | Classroom API | Low | Very High |
| 2 | Google Calendar event sync | Calendar API | Low | High |
| 3 | Deadline radar UI ("due this week") | Frontend | Low | Very High |
| 4 | Daily Telegram digest (7:30 AM) | Telegram Bot | Low | Very High |
| 5 | NL Q&A ("what's due tomorrow?") | Groq RAG | Medium | High |
| 6 | Assignment deadline 48h/24h/6h alert | Alert Engine | Medium | Very High |
| 7 | Student profile (year, branch) | Auth + DB | Low | Medium |
| 8 | PWA installable on mobile | Frontend | Low | High |

> **Gate to Phase 2:** ≥40% W4 retention + ≥30% digest open rate

### Phase 2 — Expanded (Weeks 13–24, verified OAuth, 100–2000 users)

| # | Feature | Notes |
|---|---------|-------|
| 9 | College website circular scraper | Per-college custom parser (IITdh portal, VOAL) |
| 10 | PDF/image parsing (mess menu, notices) | Gemini 2.0 Flash-Lite multimodal |
| 11 | Priority-based inbox (HIGH/MEDIUM/LOW) | Intelligence layer |
| 12 | Gmail integration (email classification) | Restricted scope — accept $540/yr CASA now |
| 13 | Email summarization + action extraction | Groq |
| 14 | Duplicate announcement deduplication | Embedding cosine similarity |
| 15 | Telegram group monitoring | Telegram Bot |
| 16 | Exam schedule extraction from PDFs | Gemini |

### Phase 3 — Intelligence (Months 5–9, freemium tier)

| # | Feature | Notes |
|---|---------|-------|
| 17 | Academic risk score (live 0–100) | Deadline density + unread count |
| 18 | Overload warning ("3 deadlines in 24h") | Rule-based + ML |
| 19 | What-if simulation ("if I attend this event…") | LLM reasoning |
| 20 | Decision recommendation engine | |
| 21 | Multi-campus support | Template per-college connectors |
| 22 | Feedback loop (deadline correction training) | Moat builder |

---

## 8. Core LLM Prompts (Production-Grade)

### Deadline Extraction (Groq, JSON mode)

```python
EXTRACTION_PROMPT = """
You are an academic deadline extractor for Indian college students.
Extract ALL deadlines, due dates, and registration deadlines from the text below.
Today's date: {today}

Return ONLY valid JSON:
{{
  "deadlines": [
    {{
      "title": "Assignment 3 submission",
      "due_at": "2026-02-18T23:59:00",
      "confidence": 0.95,
      "action_required": "submit on Google Classroom"
    }}
  ]
}}

If no deadlines found, return {{"deadlines": []}}

Text:
{text}
"""
```

### Email/Item Classification (Groq, JSON mode)

```python
CLASSIFY_PROMPT = """
Classify this student communication into ONE category.
Categories: academic | admin | event | transport | mess | placement | hostel | general

Also score relevance (0.0–1.0) for a {year} year {branch} student.

Return ONLY valid JSON:
{{"category": "academic", "relevance": 0.9, "priority": "HIGH", "one_line_summary": "..."}}

Text: {text}
"""
```

### RAG Answer Generation (Groq)

```python
RAG_PROMPT = """
You are a helpful AI assistant for IIT Dharwad students.
Answer the student's question using ONLY the provided context.
If the answer is not in the context, say "I don't have that information right now."
Be concise. Use simple English (or Hinglish if the question is in Hindi).
Always mention the source and date of information.

Context (latest first):
{context}

Student question: {question}
"""
```

---

## 9. Key API Integration Patterns

### Google Classroom Polling

```python
# connectors/classroom.py
async def sync_classroom(student: Student, creds: Credentials):
    service = build('classroom', 'v1', credentials=creds)

    courses = service.courses().list(studentId='me').execute()
    for course in courses.get('courses', []):
        coursework = service.courses().courseWork().list(
            courseId=course['id']
        ).execute()

        for work in coursework.get('courseWork', []):
            due = work.get('dueDate')
            if due:
                await upsert_deadline(
                    student_id=student.id,
                    title=work['title'],
                    due_at=parse_classroom_date(due),
                    source='classroom',
                    source_id=work['id']
                )
```

### Telegram Morning Digest Builder

```python
# alerts/digest.py
async def build_morning_digest(student: Student) -> str:
    deadlines = await get_upcoming_deadlines(student.id, days=7)
    high_items = await get_high_priority_items(student.id, hours=24)

    lines = [f"Good morning {student.name}! Here's your day:\n"]

    if deadlines:
        lines.append("📌 *Upcoming deadlines:*")
        for d in deadlines[:5]:
            days_left = (d.due_at - datetime.now()).days
            emoji = "🔴" if days_left <= 1 else "🟡" if days_left <= 3 else "🟢"
            lines.append(f"{emoji} {d.title} — {days_left}d left")

    if high_items:
        lines.append("\n⚡ *High priority today:*")
        for item in high_items[:3]:
            lines.append(f"• {item.summary}")

    lines.append("\nAsk me anything: /ask what assignments are due this week?")
    return "\n".join(lines)
```

---

## 10. Phased Roadmap (Actual Calendar)

```
WEEK 1–2 (Foundation)
  [ ] Supabase project setup, schema migration
  [ ] Google Cloud project, OAuth consent screen (sensitive scopes)
  [ ] FastAPI backend scaffold, Supabase client, auth middleware
  [ ] Telegram Bot setup (@BotFather), webhook endpoint

WEEK 3–4 (Connectors)
  [ ] Google Classroom connector (courses, coursework, announcements)
  [ ] Google Calendar connector (events, deadlines)
  [ ] APScheduler polling jobs (every 2h)
  [ ] Raw data → items table pipeline

WEEK 5–6 (Intelligence)
  [ ] Groq classifier + deadline extractor (JSON mode)
  [ ] text-embedding-3-small embedder → pgvector
  [ ] Priority scoring logic
  [ ] Deadline deduplication (by source_id)

WEEK 7–8 (Output Layer)
  [ ] Telegram Bot: morning digest (daily cron 7:30 AM)
  [ ] Telegram Bot: deadline alerts (48h, 24h, 6h)
  [ ] RAG Q&A engine (hybrid retrieval + Groq generation)
  [ ] Telegram /ask command handler

WEEK 9–10 (Frontend)
  [ ] Next.js PWA: login with Google
  [ ] Deadline radar page
  [ ] Chat Q&A interface
  [ ] Mobile-optimized, installable

WEEK 11–12 (Polish + Pilot)
  [ ] Demo data seeder
  [ ] Error handling, retry logic, rate limiting
  [ ] Trust features: source citation, low-confidence flagging
  [ ] Deploy: Vercel + Railway + Supabase
  [ ] Onboard 10 IIT Dharwad beta users

WEEK 13–20 (Phase 2 — after retention proven)
  [ ] College website scraper (IITdh portal, VOAL)
  [ ] PDF parsing with Gemini 2.0 Flash-Lite
  [ ] Gmail integration (accept CASA cost now)
  [ ] OAuth verification (sensitive scopes)
  [ ] Freemium tier split
```

---

## 11. Critical Non-Negotiable Rules

1. **Wrong deadline = sev-1 bug.** Never silently create a calendar event from low-confidence extraction (`confidence < 0.8`). Always show source + a one-tap confirm. Trust is the entire product.

2. **Never call Gmail API before Phase 2.** The $540/yr CASA audit can take weeks at the wrong time — budget it for when you have paying users or proven retention.

3. **Provider abstraction for LLMs from Day 1.** Groq had 90% of engineers move to NVIDIA in Dec 2025. Keep a `llm_client.py` wrapper so you can swap to DeepInfra / Together / Cerebras in one place.

4. **<100 users = run unverified "In Production".** Do not waste weeks on OAuth verification before you know the product retains users.

5. **Telegram over WhatsApp for now.** WhatsApp Business API = $0.006/conversation + Meta approval queue. Telegram Bot = free + instant.

6. **DPDP Act 2023 compliance** — full compliance deadline is 13 May 2027. Implement: explicit consent on signup, data-minimization (sensitive scopes only), withdrawable permissions, plain-language privacy notice. Users must be 18+.

---

## 12. Monetization Path

```
Free Tier (Growth Engine)
├── Classroom + Calendar deadline radar
├── Daily Telegram digest
├── 10 Q&A queries/day
└── Basic reminders

Premium — ₹49/mo or ₹499/yr
├── Gmail integration (email classification, deadline extraction)
├── PDF/image parsing (mess menus, circulars)
├── Academic risk score + overload warnings
├── Unlimited Q&A
└── Multi-source merge (GitHub, Telegram groups)

B2B2C — ₹50,000–₹3,00,000 / college / year  ← PRIMARY REVENUE
├── Placement cell dashboard
├── Department-wide deadline compliance view
├── Admin portal for circulars
└── Analytics: submission rates, engagement
```

> **Unit economics:** LLM cost ~₹2–5/active user/month (free tier, text-only), ~₹10–20 for premium (multimodal + Gmail). Even at ₹49/mo, gross margin per paying user is ~90%+.

---

## 13. Competitive Moat

| What Google CAN build | What Google WON'T build |
|----------------------|------------------------|
| Generic email summaries | IITdh's circular PDF parser |
| Calendar deadline radar | Your campus-specific notice format decoder |
| Generic LLM Q&A | Per-college vernacular deadline patterns |
| Horizontal AI assistant | The correction feedback dataset from 1000 students |

**The only durable moat:** deep, per-college integration + proprietary deadline extraction feedback data + campus community lock-in — college by college.

---

## 14. Key Metrics to Track

| Metric | Gate | Action if missed |
|--------|------|-----------------|
| W4 retention | ≥40% | Fix product before any growth spend |
| Digest open rate | ≥30% | Improve digest content quality |
| Free-to-paid conversion | ≥1% | Pivot hard to B2B2C institutional |
| Wrong deadline reports | 0 per week | Treat as sev-1, fix immediately |
| D7 retention | ≥25% | Fix onboarding flow |

---

## Summary

The project is solid and the research is excellent. The execution plan:

1. **Build the Classroom + Calendar deadline radar first** — skip Gmail, skip Pathway, skip all 55 features for now
2. **Deliver value via Telegram bot** — daily digest + 48h alerts. No app needed to prove retention
3. **Stack**: FastAPI + Supabase (pgvector) + Groq (Llama-3.1-8B) + Gemini Flash-Lite + Vercel + Railway
4. **Gate everything on retention** — 40% W4 before adding features
5. **Moat = per-college depth** — IITdh circular parser + correction feedback = Google can't replicate

The architecture is clean, costs are near-zero (~₹2–5/user/month), and the scope is narrow enough to ship in 12 weeks solo.

---

*Next step: Say "start Phase 1" to begin writing code — starting with Supabase schema migration and FastAPI scaffold.*
