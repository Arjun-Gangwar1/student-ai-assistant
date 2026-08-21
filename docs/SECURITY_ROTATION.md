# 🔴 Credential Rotation Runbook

**Verified live on 2026-08-22:** the Groq key and the Telegram bot token still work.
Anyone who has a copy of the old `.env.example` can use them right now.

---

## Read this first — the 90 seconds that make the rest make sense

### What actually happened

`student-ai-assistant/backend/.env.example` is a template file. Its whole purpose is to be
committed to version control so other people know which variables to set. It normally
contains fake values.

Yours contained **real ones** — the actual Supabase admin key, Google client secret, Groq
key, Telegram bot token and session signing key — and `.gitignore` did not cover it.
They sat there in plaintext from 9 June to 21 August.

### Why deleting them was not enough

A secret is not a door you can close. It is a **key that has been copied**. Once a key value
has existed anywhere it could be read — a shared folder, a backup, a screen-share, a USB
drive, a repo — you can never again prove nobody has it. The only action that restores
certainty is changing the lock: **rotation**. Deleting the file changes nothing for someone
who already copied the value.

So the question is never "do I think anyone saw it?" It is "can I *prove* nobody did?" You
cannot. So you rotate.

### The good news, and it is genuinely good

**You have zero users and an empty database.** That makes right now the cheapest moment this
will ever be:

- No student's data can have been read, because there is no student data.
- Rotating `SECRET_KEY` logs out nobody, because nobody is logged in.
- The old Supabase project is already gone, so that key is dead by accident.

Do this today and the incident closes with no consequences. Ship first and rotate later, and
you are rotating keys that have real mailboxes behind them.

### Where things stand right now

| Credential | Status today | Action |
|---|---|---|
| Supabase `service_role` | 💀 Dead — project deleted | Nothing to revoke |
| Groq API key | 🔴 **Live** (HTTP 200) | **Rotate** |
| Telegram bot token | 🔴 **Live** (`@studentai_iitdh_bot`) | **Rotate** |
| Google client secret | 🟠 Presumed live | **Rotate** |
| `SECRET_KEY` | 🟠 Exposed | **Regenerate** |
| `OPENAI_API_KEY` | 💀 Dead (HTTP 401) | Just delete the line |
| `TOKEN_ENCRYPTION_KEY` | 🟢 New, never exposed | **Back it up** |

**Total time: about 25 minutes.** Steps 1–6 in order.

---

## Before you start

```bash
cd "/home/arjun/Desktop/Student Personal Ai Assistant/student-ai-assistant/backend"
cp .env .env.rotation-backup          # so a typo is recoverable
```

> `.env.rotation-backup` matches the `.env.*` ignore rule, so it will not be committed.
> **Delete it once everything works** — it holds the old secrets.

Keep this file open in one window and your browser in another.

---

## Step 1 — Back up `TOKEN_ENCRYPTION_KEY` (2 min, do this first)

**What it is.** A key that encrypts Google OAuth tokens before they are written to the
database, so that a stolen database dump is not enough to read anyone's mail.

**Why first.** It is the one key here you must *never* lose. Every other key on this page can
be regenerated freely. This one cannot: change it and every stored token becomes permanently
undecryptable, and every user has to reconnect their Google account. Back it up before you
start editing the file it lives in.

```bash
grep '^TOKEN_ENCRYPTION_KEY=' .env
```

Copy that whole line into a password manager (Bitwarden, 1Password, KeePass — anything that
is not a text file on your desktop). Label it clearly:

> `StudentAI — TOKEN_ENCRYPTION_KEY — losing this forces all users to re-login`

**Understand the asymmetry:** losing `SECRET_KEY` is an inconvenience — people log in again.
Losing `TOKEN_ENCRYPTION_KEY` is data loss.

- [ ] Backed up somewhere I will still have access to in a year

---

## Step 2 — Groq API key (5 min) — *live, billable*

**What it is.** Your Groq API key. It bills to your account and is currently working.

**Why it matters.** Anyone holding it can run inference on your quota. On a free tier that
means your app stops working when they exhaust it. On a paid tier it means a bill.

**Do it:**

1. Go to <https://console.groq.com/keys>
2. Find the key starting `gsk_WW8vOD…` — **delete it**
3. **Create API Key** → name it `studentai-backend-2026-08` → copy the new value
4. Edit `.env` and replace the `GROQ_API_KEY=` line

> Copy the key immediately. Groq shows it exactly once; if you lose it, delete and make
> another. Naming keys by purpose and date means you can later revoke one without wondering
> what it was for.

**Verify:**

```bash
curl -s -o /dev/null -w "new key: %{http_code}\n" \
  -H "Authorization: Bearer $(grep -oP '(?<=^GROQ_API_KEY=).*' .env)" \
  https://api.groq.com/openai/v1/models
```

`200` means the new key works. If you also want to confirm the old one is dead, paste the old
value into the same command — it should return `401`.

- [ ] Old key deleted, new key in `.env`, returns 200

---

## Step 3 — Telegram bot token (5 min) — *live*

**What it is.** The token that *is* your bot, `@studentai_iitdh_bot`. Telegram has no separate
password: whoever holds this token controls the bot completely.

**Why it matters most in human terms.** With it, someone can read every message a student
sends your bot, and send messages **as your bot**. A message from a bot students trust,
saying "the exam has moved to Friday", is a far worse outcome than a surprise API bill.

**Do it:**

1. Open Telegram, message **@BotFather**
2. Send `/mybots`
3. Select **@studentai_iitdh_bot**
4. **API Token** → **Revoke current token**
5. BotFather immediately shows the new token — copy it
6. Replace the `TELEGRAM_BOT_TOKEN=` line in `.env`

> Revoking is instant and permanent. The old token dies the moment you tap it. Your bot keeps
> its username, chat history and subscribers — only the credential changes.

**Verify:**

```bash
curl -s "https://api.telegram.org/bot$(grep -oP '(?<=^TELEGRAM_BOT_TOKEN=).*' .env)/getMe" \
  | python3 -m json.tool
```

You should see `"ok": true` and `"username": "studentai_iitdh_bot"`.

**One thing to remember for later:** in production the backend re-registers its webhook at
startup, so a token change is picked up on the next deploy. Locally you use
`scripts/telegram_dev_poll.py`, which needs no webhook at all. Nothing else to do now.

- [ ] Token revoked, new token in `.env`, `getMe` returns ok

---

## Step 4 — Google OAuth client secret (7 min) — *the subtle one*

**What it is.** Half of the credential pair that proves to Google "this app is Student AI
Assistant". The client **ID** is public by design — it appears in browser URLs. The client
**secret** is what proves the request is genuinely from your server.

**Why it matters, and why it is the least obvious.** With your client ID *and* secret, someone
can stand up a site that produces a Google consent screen showing **your app's name**. A
student sees a legitimate-looking Google sign-in for an app they were told to use, approves
it, and the attacker receives tokens for that student's account. Because your scope list
includes `gmail.readonly`, that consent grants access to their mail.

That is a phishing capability wearing your name. It is why this one matters even though
nothing is deployed yet.

**Do it:**

1. Go to <https://console.cloud.google.com>
2. Select your project (top-left project picker)
3. **APIs & Services → Credentials**
4. Under **OAuth 2.0 Client IDs**, open the one whose ID starts `247279631192-…` and ends
   `…j67n82.apps.googleusercontent.com`
5. On the right, find **Client secrets** → **Add secret**
6. Copy the new secret, then **disable and delete the old one**

> Newer Google Cloud consoles let you hold two secrets briefly so a live app can roll over
> without downtime. If yours only offers **Reset Secret**, that is fine — it invalidates the
> old one immediately, which costs nothing with no users.

7. Replace `GOOGLE_CLIENT_SECRET=` in `.env`. **Leave `GOOGLE_CLIENT_ID` unchanged** — it is
   not secret and does not rotate.

**While you are in there, check two things that will bite you at deploy time:**

- **Authorised redirect URIs** must contain exactly `http://localhost:8000/api/auth/callback`,
  matching `GOOGLE_REDIRECT_URI` in `.env` character for character. Google compares these as
  exact strings — a trailing slash is a different URI. When you deploy, add the production
  callback as a second entry.
- **OAuth consent screen → Publishing status.** While "Testing", only accounts on the **Test
  users** list can sign in, capped at 100. Add your own email there now, or your first real
  login attempt will fail with `access_denied` and look like a code bug.

**Verify:**

```bash
cd "/home/arjun/Desktop/Student Personal Ai Assistant/student-ai-assistant/backend"
./venv/bin/python -c "
from app.config import settings
s = settings.google_client_secret
print('client secret loaded:', s[:7] + '…' if s else 'MISSING')
print('redirect URI       :', settings.google_redirect_uri)
"
```

The real test is a working sign-in, which comes in Step 7.

- [ ] New secret in `.env`, old one deleted, redirect URI and test users checked

---

## Step 5 — `SECRET_KEY` (2 min)

**What it is.** The key that signs session cookies. When you sign in, the server sends a
cookie saying "this browser is student `abc-123`", signed with this key so it cannot be
altered.

**Why it matters.** Anyone with this key can **forge a valid session for any student id** and
read that student's data through the API — no password, no OAuth, nothing to detect. It is a
skeleton key for user accounts.

**Do it:**

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Replace the `SECRET_KEY=` line in `.env` with the output.

> This invalidates every existing session. Right now that is nobody. Once you have users,
> rotating this logs everyone out — which is exactly what you *want* it to do in an incident,
> but plan it for a quiet hour.

- [ ] Regenerated

---

## Step 6 — `OPENAI_API_KEY` (1 min) — *just delete it*

**What it is.** Nothing, any more. Embeddings run locally through `sentence-transformers`, so
no OpenAI key is needed. The value in your file returns **HTTP 401** — it is already dead, and
its format does not match OpenAI's `sk-…` convention, so it may never have been an OpenAI key
at all.

**Why remove it.** An unused credential is pure liability: it can leak but can never help.

```bash
cd "/home/arjun/Desktop/Student Personal Ai Assistant/student-ai-assistant/backend"
sed -i '/^OPENAI_API_KEY=/d' .env
grep -c '^OPENAI_API_KEY=' .env    # expect 0
```

If you recognise it as a key for some *other* service, go and revoke it there.

- [ ] Line removed

---

## Step 7 — Verify everything still works

```bash
cd "/home/arjun/Desktop/Student Personal Ai Assistant/student-ai-assistant/backend"

# 1. Config loads and nothing required is missing
./venv/bin/python -c "
from app.config import settings
problems = settings.validate_runtime()
print('\n'.join('⚠ ' + p for p in problems) if problems else '✓ configuration is complete')
"

# 2. No live credentials in anything tracked by git
python3 ../scripts/check_secrets.py

# 3. Full test suite
./venv/bin/python -m pytest -q
```

Expect: no config warnings, a green secret scan, and 114 passing tests.

Then the real proof — start the app and sign in with Google:

```bash
./venv/bin/python -m uvicorn app.main:app --reload
# open http://localhost:3000 in another terminal with: cd ../frontend && npm run dev
```

A successful sign-in exercises the new Google secret, the new `SECRET_KEY`, and
`TOKEN_ENCRYPTION_KEY` all at once. (This needs a database — see the Next section.)

- [ ] Config clean, secrets clean, tests green, sign-in works

---

## Step 8 — Clean up and make it stick

```bash
# Remove the backup holding your old secrets
rm .env.rotation-backup

# Run the secret scanner automatically before every commit
cd "/home/arjun/Desktop/Student Personal Ai Assistant"
ln -sf ../../student-ai-assistant/scripts/check_secrets.py .git/hooks/pre-commit
chmod +x student-ai-assistant/scripts/check_secrets.py
```

The hook blocks any commit containing something credential-shaped. It matches on *shape*
(`gsk_…`, `GOCSPX-…`, JWTs, bot tokens), not on the specific values above, so it keeps working
after rotation and catches keys for services you have not added yet.

- [ ] Backup deleted, pre-commit hook installed

---

## Three habits that prevent the next one

**1. Real values live in `.env`, and `.env` is never committed.** Every other file gets a
placeholder. If you are typing a real secret into a file that is not `.env`, stop.

**2. Never paste a real credential — even a fragment — into code, tests, or docs.**
A truncated key is still a real key fragment, and git remembers everything. Use obviously fake
values: `gsk_FAKEKEY0123…`. *(This one had to be fixed in this project's own test file.)*

**3. Secrets go in a password manager, not in chat.** Not WhatsApp, not Telegram-to-self, not
a note. When you deploy, Railway and Vercel each have an encrypted environment-variable
store — put them there, never in `railway.json` or `vercel.json`.

---

## About the git history

Good news: `.env.example` was scrubbed **before** the repository's first commit, so none of the
leaked values were ever committed. `git log -p --all` is clean.

This matters because a secret in git history is genuinely hard to remove — it survives in every
clone, fork and CI cache, and requires rewriting history with `git filter-repo` or BFG. You do
not have that problem. Keep it that way: **the pre-commit hook in Step 8 is what keeps it
true.**

When you push to GitHub, enable **Settings → Code security → Secret scanning** and **Push
protection**. GitHub then blocks pushes containing recognised credential formats — a free
second net under the local hook.

---

## Why the token encryption in Step 1 exists at all

`students.google_tokens` used to store Google **refresh tokens** as plaintext JSON.

A refresh token does not expire. It is not a session — it is standing permission to mint fresh
access tokens indefinitely. Combined with the exposed `service_role` key, a single database
read would have granted a stranger ongoing access to every connected student's Gmail,
Classroom and Calendar, **with no sign-in alert on their side**, until each student manually
revoked it in their Google account settings.

Migration `001_baseline.sql` replaces that column with `google_tokens_enc`, written through
Fernet (AES-128-CBC with an HMAC tag) in `app/utils/crypto.py`. A database leak alone is now
insufficient — the attacker also needs `TOKEN_ENCRYPTION_KEY`, which exists only in the
environment.

That is the whole reason Step 1 says back it up before touching anything else.

---

## Next: you still need a database

Rotation does not unblock the app — the Supabase project is gone. Pick one:

```bash
# Option A — local Postgres (needs Docker access; see README)
sudo usermod -aG docker $USER && sudo apt install docker-compose-plugin
# log out and back in, then:
cd student-ai-assistant && docker compose up -d db redis
cd backend && ./venv/bin/python scripts/migrate.py

# Option B — new Supabase project
# supabase.com → New project → Settings → Database → Connection string (URI)
# put it in DATABASE_URL, then:
cd backend && ./venv/bin/python scripts/migrate.py
```

Either way, `scripts/migrate.py` builds the whole schema. Then
`python ../scripts/seed_demo_data.py` gives you something to look at.
