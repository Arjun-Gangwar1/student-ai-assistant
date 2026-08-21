# Student Personal AI Assistant: A Strategic Build-and-Scale Guide (India-First, 2026)

## TL;DR
- **The problem is real but the wedge is wrong.** Academic information overload and missed deadlines are genuine pains for Indian students, but the founder's instinct to build "55 features" across Gmail + Classroom + Calendar + burnout-scoring is a trap. The single sharpest, cheapest, most-defensible first feature is a **"What's due this week?" deadline radar built on Google Classroom + Calendar data — deliberately avoiding Gmail at first** — because Classroom/Calendar are merely "sensitive" OAuth scopes while Gmail is "restricted" and triggers an expensive annual CASA security assessment.
- **Pathway is not needed.** At pilot and early scale, event-driven webhooks (Gmail push via Pub/Sub) + scheduled polling (Classroom/Calendar) + Postgres/pgvector on Supabase is sufficient, far cheaper, and easier to operate. LLM cost per active student is near-zero (~₹2–5/month) using Llama 3.1-8B on Groq for text and Gemini Flash-Lite for multimodal — so the binding constraints are OAuth verification, distribution, and retention, not compute.
- **"Untouchable for 5 years" is not realistic; a 2–3 year India-campus head-start is.** No solo founder can out-engineer Google. The only durable moat is **deep, per-college integration + proprietary deadline-extraction feedback data + campus community/ambassador lock-in**, college by college. Validate ruthlessly before scaling: if you cannot get 40%+ weekly retention in a single-campus pilot, do not proceed.

## Key Findings

1. **Demand is real and painful, but "deadline-missing" needs first-party validation.** India recorded 14,488 student suicides in 2024 (8.5% of the national total, up 4.3% from 2023) with academic pressure as a leading driver; broad academic-overload and procrastination statistics exist. But almost none of this directly measures "missed a deadline because info was buried in email/portals." The founder must generate that evidence at their own campus.
2. **The competitive field is crowded at the edges but empty in the middle.** Reclaim, Motion, Shortwave, Superhuman, Notion AI, and Google's own Gemini all solve adjacent problems for professionals — none is built for the *Indian college student's academic logistics* (Classroom assignments, college circulars, vernacular notices, mess menus). College ERPs (Samarth, 200+ HEIs) and edtech (PW, Unacademy) are content/admin plays, not personal-assistant plays. There is a genuine unoccupied niche.
3. **Google API compliance is the #1 real-world blocker — and there is a clean path around it.** Gmail's `gmail.readonly` is a *restricted* scope that forces an annual third-party CASA security assessment (~$540/yr minimum via TAC Security) if data touches your servers. Classroom and Calendar scopes are merely *sensitive* — verification only, no CASA. A <100-user pilot needs no verification at all.
4. **Compute is cheap; the model stack is a solved problem.** Llama 3.1-8B on Groq costs $0.05/$0.08 per million input/output tokens; Gemini 2.0/2.5 Flash-Lite costs $0.075–0.10/$0.30–0.40. Batching and prompt-caching cut this ~75%.
5. **Willingness to pay is very low.** Indian students anchor to Spotify Premium Student at ₹69/month (promo as low as ₹59). A productivity app's realistic ceiling is ₹49–99/month, with ~1–3% free-to-paid conversion. B2B2C (college/department licensing) is the more credible revenue path.

## Details

### 1. Problem Validation & Demand

**The macro evidence is strong on stress, weaker on the specific "logistics" pain.** India recorded 14,488 student suicides in 2024 (7,669 male, 6,819 female; 8.5% of the national total of 1,70,746), a record high per NCRB's "Accidental Deaths and Suicides in India 2024" report, as reported by The Wire (11 May 2026). Academic pressure is repeatedly cited as a leading driver; one widely circulated figure is that 61% of suicide attempts at IITs were linked to academic stress. Nearly half of college students report academic overload and procrastination in US studies (~44.5%), and email-overload research finds professionals lose ~10.8 hours/week to non-critical email and that 68% say email overload contributes to burnout. Around 65% of students report skipping an assignment at least once due to overwhelm.

**The honest gap:** none of this proves students miss deadlines *because* information is fragmented across Gmail/Classroom/WhatsApp/college portals. That is the founder's core hypothesis and it is *unvalidated*. The stress data tells you the emotional stakes are high (good for messaging); it does not tell you students will adopt a deadline tool.

**Cheap validation playbook (4–6 weeks, ~₹0–5,000):**
- **15–20 problem interviews** at IIT Dharwad using "The Mom Test" method — ask about past behavior ("walk me through the last deadline you missed"), never pitch. Decision gate: do ≥60% describe a specific, recent, painful instance unprompted?
- **A "fake-door" landing page** (Carrd/Vercel) describing the deadline radar + a waitlist email capture. Push via campus WhatsApp/Instagram groups. Gate: ≥100 signups and ≥25% click on "how it works" from one campus.
- **A manual "concierge MVP":** for 10 volunteers, manually read their Classroom + circulars for two weeks and WhatsApp them a "what's due" digest each morning. This validates value with zero code. Gate: do ≥5 of 10 say they'd be "very disappointed" without it (Sean Ellis PMF test)?
- **Validation metrics that matter:** % who return unprompted (D7/D30 retention), digest open rate, "very disappointed" score ≥40%, and unsolicited referrals. Vanity metrics (signups, pageviews) do *not* count as validation.

### 2. Competitive Landscape

- **General AI/email/calendar assistants:** Reclaim.ai (free tier; paid from ~$8–10/mo; acquired by Dropbox, announced 20 August 2024 — at the time Reclaim served 320,000+ users across 43,000+ companies and had raised $9.5M; no native mobile app, PWA only), Motion (~$29/mo individual; no free plan), Shortwave (free → ~$7–30/mo), Superhuman (~$40/mo; acquired by Grammarly in 2025 — Grammarly's parent rebranded to "Superhuman" on 29 Oct 2025 and the app is now "Superhuman Mail"), Notion AI. All target *working professionals*, price in dollars, and ignore Classroom/college-circular data. Google's own Gemini in Workspace can summarize email and is the existential threat (see Moat).
- **Student productivity/study apps:** My Study Life, Todoist, Notion for students, academic planners — all *manual entry*. None auto-ingest the student's actual academic data sources. That manual-entry friction is precisely the wedge.
- **Indian edtech & ERP:** Physics Wallah (profitable, IPO'd, tier-2/3 vernacular strength), Unacademy (total revenue down 16% YoY to ₹826.3 Cr in FY25 from ₹988 Cr, net loss cut 31% to ₹436 Cr per a document reviewed by Entrackr; valuation fell from ~$3.5B in 2021 to under $500M, with upGrad signing a term sheet to acquire it in an all-stock deal in March 2026), BYJU'S (collapsed). These are *content* businesses. College ERPs like Samarth eGov (200+ HEIs, 40+ central/state universities, 100+ colleges) are *administrative* systems students are forced to use — clunky, not assistant-like.
- **The gap:** No one owns "academic logistics intelligence" — turning the messy stream of assignments, circulars, deadlines, and notices into a proactive personal assistant for the Indian student. **Why no one dominates it:** it's unglamorous, the data is fragmented and college-specific (hard to generalize), OAuth/compliance is a moat-in-reverse that scares hobbyists, and the willing-to-pay economics are poor — so big players ignore it and hobbyists can't get past verification.

### 3. Moat & Defensibility — A Candid Assessment

**Blunt truth: "the #1 player, untouchable for 5 years" is not achievable for a solo student founder against Google.** Google can replicate deadline-extraction from Gmail/Classroom as a Gemini feature whenever it chooses. Anyone telling the founder otherwise is selling hype. What *is* achievable is a **2–3 year head start in a niche too small and unglamorous for Google to prioritize**, defended by moats Google structurally won't build:

- **Deep college-specific integration (strongest moat):** Google will never scrape *your specific college's* notice board, mess menu, exam circular PDFs, or department WhatsApp broadcasts. Per-college connectors and parsers are tedious, local, and exactly what a motivated student founder can do that a global platform won't.
- **Proprietary feedback loop:** every "is this actually a deadline? did the date parse correctly?" correction trains a private dataset of how Indian academic communications encode deadlines (Hinglish, "submit by EOD tomorrow," circular formats). This compounds and is not portable to a competitor.
- **Community/switching costs:** campus ambassador networks, club partnerships, and a per-college "my whole batch uses it" effect create local network density. Switching means re-onboarding your social graph.
- **Institutional partnerships:** a signed pilot with a department/college (even unpaid) is a relationship Google's self-serve model won't pursue and a competitor can't easily dislodge.

**The strategy that survives the Google threat:** stay narrow and hyper-local, win Indian campuses one at a time, accumulate per-college integration depth and correction data, and convert that into institutional relationships. Do **not** try to be a horizontal "AI assistant" — that's where Google wins. Be the thing that knows *IIT Dharwad's* academic rhythm better than any global tool ever will, then repeat.

### 4. Tech Architecture for Low Cost & Scale (2026)

**(a) Do you need Pathway? No.** Pathway is a real-time incremental-indexing streaming engine — genuinely useful only when a knowledge base changes constantly and freshness in seconds matters. A student deadline tool's data changes a few times a day. Use:
- **Gmail (later phase):** push notifications via `users.watch` → Google Cloud Pub/Sub → webhook, with `historyId` cursors for incremental sync. (Note: practitioners report Pub/Sub `watch` must be renewed at least every 7 days — recommend daily — and occasionally needs a polling fallback when notifications silently stop; budget for that reliability work.)
- **Classroom/Calendar:** scheduled polling (cron, every few hours) + incremental sync. Simple and robust.
- **Storage/index:** Supabase (Postgres + pgvector, free tier → $25/mo Pro). Hybrid keyword + vector retrieval in one database. This replaces Pathway, Pinecone, and a separate vector DB.
- **Orchestration:** plain Python + a thin LangChain/LangGraph layer only where it earns its keep. Avoid heavy frameworks early.

**(b) LLM strategy — cheapest viable in 2026:**
- **Text (classification, extraction, Q&A):** Llama 3.1-8B Instant on Groq — $0.05/M input, $0.08/M output, ~560–840 tokens/sec. Use JSON mode for structured deadline extraction.
- **Multimodal (rubric/circular/mess-menu PDFs & images):** Gemini 2.5 Flash-Lite or 2.0 Flash-Lite — $0.075–0.10/M input, $0.30–0.40/M output, with a usable free tier.
- **Cost levers:** Groq Batch API (−50%) for nightly classification; prompt caching (−50%) for the repeated system prompt; together ~25% of on-demand. Provider risk note: ~90% of Groq's engineering staff (and founder Jonathan Ross) moved to NVIDIA in a Dec 2025 deal, though GroqCloud still operates independently — keep a provider-abstraction layer (the same Llama model is available via Together, Cerebras, DeepInfra at comparable or lower prices; DeepInfra lists 8B as low as ~$0.02–0.03/M).

**(c) Vector DB:** pgvector inside Supabase (free, included) is sufficient to ~100K vectors. Qdrant/Chroma self-hosted or Pinecone free tier are alternatives, but a second datastore is unnecessary complexity early.

**(d) Hosting on a low budget:** Vercel (frontend) + Supabase (DB/auth/edge functions) + a small always-on worker on Fly.io/Railway/Render for the Pub/Sub webhook and cron. Cloudflare Workers + R2 for edge/static. Apply for credits: Microsoft for Startups Founders Hub (~$1–5K, no VC needed), Cloudflare for Startups ($5K+), Google for Startups Cloud Program (up to ~$200K+ if accepted), Supabase ($1–10K). Oracle Cloud Always Free as a fallback always-on VM. Caution: free tiers can have "cliffs" (e.g., MongoDB Atlas M0 connection limits, AWS 12-month expiry) — prefer tiers that grow with you (Cloudflare Workers/R2, Supabase, Neon).

**(e) Mobile vs web:** Indian students are mobile-first, but native apps are costly to build/maintain solo and the OAuth flow is identical on web. **Start with a mobile-optimized PWA** (installable, push notifications via web push) — Reclaim itself ships only a PWA. Add a thin native shell (Expo/React Native) only after retention is proven. Delivery of the daily digest via **WhatsApp** is likely more important than any app UI for India.

**(f) Per-user cost:** A typical student receiving ~10 academic items/day generates ~300 classification calls/month (~600 tokens each) plus ~30 Q&A queries — roughly **$0.02–0.05 (₹2–4) per active user per month** in LLM cost even before batching/caching. **What drives cost as you scale:** (1) free-tier abuse / inactive accounts still being polled — gate polling on active users; (2) multimodal PDF volume — cache parsed results; (3) CASA + infra fixed costs once you cross 100 users and add Gmail; (4) WhatsApp Business API messaging fees if you use the official API at scale.

### 5. Google API / Compliance Reality (Critical)

This is the make-or-break operational constraint, and the research yields a clear, exploitable asymmetry confirmed against Google's official documentation:

- **Scope classification (decisive):** `gmail.readonly` and `gmail.metadata` are **RESTRICTED** scopes — explicitly on Google's restricted-scopes list (support.google.com/cloud/answer/13464325). Classroom scopes (`classroom.courses.readonly`, `classroom.coursework.me.readonly`, `classroom.announcements.readonly`) and `calendar.events` are merely **SENSITIVE** — they do *not* appear on the restricted list and do *not* trigger CASA. Google's own sensitive-scope documentation uses "reading events stored in Google Calendar" as its canonical example of a *sensitive* (not restricted) scope.
- **What "restricted" costs you:** if your app stores or transmits restricted-scope (Gmail) data on/through your servers, you must pass an **annual third-party CASA security assessment**. Tier 2 via TAC Security (Google's preferred, India-based lab) is **$540/app/year** under the basic plan (a discounted rate negotiated by Google), ~$720 for a "Premium" plan with unlimited rescans, and ~$1,800 for an "Enterprise Tier 2" plan; Tier 3 is ~$4,500. This is on top of OAuth verification (consent screen, privacy policy on a first-party domain, demo video, brand verification), which can take several weeks. Note Google's CASA team ceased the old PwC self-scan portal and now routes developers to paid assessors.
- **The 100-user cap:** an unverified app can have at most **100 users who grant the sensitive/restricted scopes, cumulative over the project's entire lifetime, non-resettable** (per support.google.com/cloud/answer/15549945 and the verification FAQ). Crucially, you must publish "In Production" (unverified) — "Testing" mode expires user authorizations/refresh tokens every 7 days and is unusable for a real pilot.
- **The CASA "no servers" exception is real but unsafe to rely on:** policy text ties the assessment to apps that "store or transmit restricted scope data on servers" / have "the capability to access Google user data from or through a server." A pure client-side/on-device architecture *could* therefore avoid CASA, but Google does not publish an explicit on-device exemption, the "capability to access via a server" language is a reviewer loophole, and there is a 2025 Google Cloud Community report of Google denying the local-only carve-out for a Gmail iOS app. Verification is still required regardless. Do not bet the company on it.

**The practical path (this is the strategic unlock):**
1. **Pilot (<100 users): no verification, no CASA.** Run the home-campus pilot "In Production," unverified, under 100 lifetime users, indefinitely.
2. **Scale on SENSITIVE scopes only.** Build the core product on **Classroom + Calendar** — sensitive scopes that require OAuth verification but **no CASA security assessment**. This lets you scale to thousands of users for the cost of a verification review, not a $540+/yr audit.
3. **Add Gmail last, deliberately.** Only when Gmail-derived deadlines prove necessary, accept the restricted-scope CASA cost (~$540/yr) — by then you should have revenue or funding. Consider minimizing scope (metadata-only, or processing client-side) to reduce the assessment burden.

### 6. Monetization & Unit Economics

**Willingness to pay is the hard ceiling.** Indian students anchor to Spotify Premium Student at **₹69/month** per Spotify's official India student page (promotional rate as low as ₹59 after a free trial — roughly 50% off standard). A new productivity app cannot exceed that; realistic premium pricing is **₹49–99/month** or **₹399–699/year** (annual heavily preferred — students pay once per semester, not monthly). Expect **1–3% free-to-paid conversion**.

**Recommended freemium split:**
- **Free forever (core):** deadline radar for Classroom + Calendar, daily "what's due" digest (in-app + WhatsApp), natural-language Q&A, basic reminders. This must be genuinely useful — it's your growth engine.
- **Premium (₹49–99/mo or ₹499/yr):** Gmail integration (auto-extract deadlines from professor emails), multimodal PDF/image parsing (rubrics, circulars), the "academic risk score"/overload predictor, smart adaptive reminders, multi-source merge (GitHub, Telegram, college portal), and priority/early features.

**Unit economics (per active user/month):** LLM/API cost ~₹2–5 (free tier, text-only) rising to ~₹10–20 for premium (multimodal + Gmail). Even at ₹49/mo, gross margin per paying user is ~90%+. **The problem is not margin — it's conversion volume.** At 1.5% conversion and ₹49/mo, 100,000 users → 1,500 payers → ~₹73,500/mo (~$880) gross revenue. That is a lifestyle-business trajectory on consumer pricing alone.

**The better revenue path — B2B2C:** license to **colleges/departments** (₹50,000–₹3,00,000/college/year for an institution-wide "student success / deadline-compliance" deployment), to **placement cells** (deadline-compliance + an internship/career feature), or **sponsorships** (ed-brands reaching engaged students). One signed college can exceed thousands of individual subscriptions and aligns with the institutional-funding path the founder already wants. This is also the most defensible revenue (relationship + switching cost). The edtech market lesson is unambiguous: Physics Wallah survived by building unit economics first; BYJU'S/Unacademy nearly died chasing growth — so prioritize sustainable economics over vanity scale.

### 7. Go-to-Market & Growth

- **0 → 100 (home campus, IIT Dharwad):** manual concierge + PWA. Recruit through your own hostel/department, clubs, and class WhatsApp groups. Hand-onboard every user. Goal: prove retention, not scale. (Tinder's playbook: it hit ~99.5% daily retention with a few hundred users *before* going to campuses — get retention right first.)
- **100 → 1,000 (saturate home campus + 1–2 nearby):** campus ambassador program (proven playbook: Tinder, Bumble, Red Bull globally, and in India OnePlus/Unstop/Google DSC/Gemini Student Ambassadors). Pay ambassadors in incentives/certificates (₹5,000-style response targets are standard) + a leaderboard. Partner with official student clubs and the placement cell. Seed Instagram/WhatsApp study communities.
- **1,000 → 100,000 (college-by-college expansion):** template the per-college integration (each new college = a connector config + a few circular-format parsers). Launch each campus via a local ambassador + a "your whole batch is on it" referral mechanic. Prioritize IITs/NITs first (homogeneous Google Workspace + Classroom usage, high digital literacy, tight networks), then private universities, then state universities (where vernacular/Hinglish support matters most).
- **Virality mechanics:** shareable "what's due this week" cards, "invite your batchmates to sync the same class" group value, and exam-season spikes. Distribution via WhatsApp is the single most important Indian growth channel.

### 8. Phased Roadmap

- **Phase 0 — Validation (Weeks 1–6; cost ~₹0–5K).** 15–20 Mom-Test interviews, fake-door landing page, 10-person concierge digest. **Gate to proceed:** ≥40% "very disappointed" PMF score + ≥5/10 concierge users retaining weekly. *If you fail this gate, stop or pivot — do not build.*
- **Phase 1 — MVP (Weeks 7–14).** Narrowest valuable slice: **Classroom + Calendar deadline radar + daily WhatsApp/PWA digest + NL Q&A**, sensitive-scopes only, "In Production" but unverified, <100 users, home campus. **Gate:** ≥40% W4 retention, ≥30% digest open rate.
- **Phase 2 — Home-campus pilot & iterate (Weeks 15–24).** Complete OAuth verification (sensitive scopes), push to a few hundred users, add per-college circular parsing. **Gate:** retention holds with verified app + >100 users; ≥1 unsolicited "can my friends at [other college] use this?"
- **Phase 3 — Freemium launch + 2nd/3rd campus (Months 7–10).** Ship premium tier (Gmail + multimodal + risk score; accept CASA cost now). Launch ambassador program. **Gate:** ≥1% conversion OR a signed college/department pilot.
- **Phase 4 — Multi-campus scale + funding (Months 11–18).** Template per-college onboarding; pursue institutional licensing; raise from college incubator/E-cell, government student-startup schemes, or angels using retention + B2B2C traction. **Gate for raising:** demonstrable multi-campus retention + a repeatable per-college acquisition cost.

### 9. Risks & Honest Pitfalls

- **Google deprecates/expands access or ships parity in Gemini.** *Mitigation:* don't depend on Gmail early (use Classroom/Calendar); own the per-college data Google won't touch; build community lock-in.
- **OAuth/CASA friction kills momentum.** *Mitigation:* the sensitive-scopes-first path above; budget time for verification; keep Gmail (restricted) for later/premium.
- **Low willingness to pay.** *Mitigation:* lead with B2B2C/institutional revenue; keep per-user cost near-zero so free tier is sustainable; annual pricing.
- **Exam-season churn (students leave after exams).** *Mitigation:* the product is most valuable *during* the semester; make it a year-round habit (timetable, internship deadlines, club events), not just exam reminders.
- **Accuracy/hallucination erodes trust on deadlines (fatal for this category).** *Mitigation:* never silently auto-create events from low-confidence extractions; always show source + a one-tap confirm; treat a wrong deadline as a sev-1 bug. Trust is the entire product.
- **Privacy backlash / DPDP non-compliance.** *Mitigation:* DPDP Act 2023 + DPDP Rules 2025 (notified 13 Nov 2025) apply, with full compliance required by 13 May 2027. You are a Data Fiduciary processing personal data: implement explicit, granular, withdrawable consent; a plain-language privacy notice; data minimization; 72-hour breach-reporting readiness; and note that users under 18 require *verifiable parental consent* — so target 18+ college students and verify age. The Act gives startups a lighter graded burden, and data-minimization (sensitive scopes, on-device where possible) doubles as your DPDP and Google-trust story.
- **Solo-founder burnout.** *Mitigation:* narrow scope (one feature, one campus); automate ops; recruit a co-founder or first ambassador-turned-teammate before multi-campus scale.

### 10. The Narrow Wedge — The One Thing to Build First

**Build exactly this: a "What's due this week?" deadline radar for one campus, powered by Google Classroom + Calendar (not Gmail), delivered as a daily WhatsApp/PWA digest with natural-language Q&A.**

Why this and nothing else first:
- **Immediately lovable:** answers the one question every student asks weekly, with zero manual entry — the differentiator from My Study Life/Todoist.
- **Cheap to build:** Classroom + Calendar APIs, Llama-8B extraction, pgvector Q&A. ~₹2–4/user/month.
- **Compliance-light:** sensitive scopes only → no CASA, and a <100-user pilot needs no verification at all.
- **Defensible seed:** every correction trains your private deadline-extraction dataset; every campus adds per-college parsing depth.
- **Expandable:** Gmail, multimodal circulars/rubrics, the risk score, GitHub/Telegram all become *premium upgrades* later — not launch requirements.

Resist building the burnout/"academic risk score" first: it's the most speculative feature, the hardest to make accurate, and the easiest to erode trust with. Earn the right to predict overload by first nailing the boring, beloved deadline radar.

## Recommendations (Prioritized "What To Do Next")

1. **This week:** Drop "Parsec/Singularity." Write the one-sentence wedge ("Never miss a deadline — your class assignments and college events, in one daily digest"). Stand up a Carrd/Vercel waitlist page.
2. **Weeks 1–6 (validation gate):** Run 15–20 Mom-Test interviews + a 10-person manual WhatsApp concierge digest at IIT Dharwad. **Only proceed if ≥40% would be "very disappointed" without it.**
3. **Weeks 7–14:** Build the MVP on **Classroom + Calendar only** (sensitive scopes), "In Production" but unverified, <100 users. Stack: Python + Supabase (pgvector) + Groq Llama-8B + Gemini Flash-Lite + Vercel PWA + WhatsApp digest. Skip Pathway.
4. **Weeks 15–24:** Complete OAuth verification for sensitive scopes; iterate to ≥40% W4 retention; add per-college circular parsing. Apply for Microsoft/Cloudflare/Google startup credits now.
5. **Months 7–10:** Launch freemium (premium = Gmail + multimodal + risk score; accept ~$540/yr CASA only now). Start a campus ambassador program; pitch your placement cell/department for a paid or reference pilot.
6. **Months 11–18:** Template per-college onboarding; pursue B2B2C institutional licensing as the primary revenue; raise from your college E-cell/incubator or student-startup schemes on multi-campus retention.
7. **Always:** Treat a wrong deadline as a sev-1 trust bug; keep per-user cost near-zero; stay narrow and hyper-local; assume Google could enter and make per-college depth + community your moat.

**Benchmarks that change the plan:** If validation PMF <40% → pivot the wedge. If W4 retention <30% after MVP → fix retention before any growth spend. If free-to-paid <1% after freemium launch → pivot hard to B2B2C/institutional licensing and treat consumer as pure acquisition.

## Caveats
- Several demand statistics (suicide figures, "61% of IIT attempts," US procrastination/email-overload rates) are macro/contextual and largely from secondary reporting; they establish stakes, not direct demand for *this* product. First-party campus validation is non-negotiable.
- OAuth scope classifications and the CASA "no-servers" exception are interpreted by Google reviewers case-by-case; treat the sensitive-vs-restricted distinction as the plan's foundation but verify current classification in your own Cloud Console before building, since Google has changed CASA processes mid-stream.
- LLM pricing and provider stability (Groq's NVIDIA transition, Gemini tier changes) shift frequently; keep a provider-abstraction layer.
- Unit-economics and conversion figures are order-of-magnitude estimates from public token pricing and typical usage/benchmark assumptions, not measured from a live cohort.
- DPDP Rules operational deadlines (e.g., consent-manager framework, full compliance by 13 May 2027) are still phasing in; monitor for the official Significant Data Fiduciary list and any education-sector guidance.