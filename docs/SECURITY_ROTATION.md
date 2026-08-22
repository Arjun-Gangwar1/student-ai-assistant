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

## Step 4 — Google OAuth client (15 min) — *the fiddly one*

**What it is.** A pair of values that prove to Google "this app is Student AI Assistant". The
client **ID** is public by design — it appears in browser URLs. The client **secret** proves
the request genuinely came from your server.

**Why it matters most subtly.** With your client ID *and* secret, someone can stand up a site
that shows a Google consent screen carrying **your app's name**. A student sees a legitimate
sign-in for an app they were told to use, approves it, and the attacker receives tokens for
their account. Because the scope list includes `gmail.readonly`, that consent hands over their
mail. It is a phishing capability wearing your name.

---

### 4a. First, find the right project

Your app is configured for project number **`247279631192`** — that is the digit string before
the first dash in `GOOGLE_CLIENT_ID`.

At <https://console.cloud.google.com>, click the project picker at the top-left. If the picker
shows a different number (for example `424319561492`, "Project 1"), you are in the wrong
project and its credentials are unrelated to this app.

Search the picker for `247279631192`. Then:

- **Found it** → open it and go to **4b**.
- **Not there** → it belongs to a different Google account, or it was deleted. Sign in with the
  account you originally used, or go to **4c** and create a fresh client. Creating a new one is
  completely fine; nothing is lost.

---

### 4b. If you found the original project — rotate the secret

1. **APIs & Services → Credentials**
2. Under **OAuth 2.0 Client IDs**, open the entry whose ID starts `247279631192-…`
3. Check **Application type** at the top. It must be **Web application**.
   - If it says **Desktop**, stop and use **4c** instead — see the box below for why.
4. Under **Client secrets**, choose **Add secret** (newer consoles) or **Reset secret** (older).
5. Copy the new secret, then delete the old one.
6. Replace `GOOGLE_CLIENT_SECRET=` in `.env`. **Leave `GOOGLE_CLIENT_ID` alone** — it is not
   secret and does not rotate.

Now jump to **4d** to verify the surrounding configuration.

---

### 4c. Creating a new OAuth client

> #### Why the type matters
>
> **Desktop** clients are *public clients*: Google assumes the secret ships inside an
> application a user can decompile, so it is not treated as confidential, and you cannot
> configure custom redirect URIs.
>
> **Web application** clients are *confidential clients*: the secret lives on a server nobody
> else can read, and you register exactly which redirect URIs are allowed. That is what this
> app is — a FastAPI backend. The code confirms it: `CLIENT_CONFIG` in `app/api/auth.py` uses
> the `"web"` key, and the flow redirects to a fixed URL on your own server.
>
> Using a Desktop client here would be the wrong security model even where it happens to work.

**Step 1 — Enable the APIs you will call.** Easy to forget, and its failure looks like a code
bug: *"Google Classroom API has not been used in project … before or it is disabled."*

**APIs & Services → Library**, then search for and **Enable** each of:

- `Google Classroom API`
- `Google Calendar API`
- `Gmail API`

**Step 2 — Configure the consent screen** (only needed once per project).

**APIs & Services → OAuth consent screen**:

| Field | Value |
|---|---|
| User type | **External** |
| App name | `Student AI Assistant` — this is what students see, so make it recognisable |
| User support email | your IITDh address |
| Developer contact | your IITDh address |
| Authorised domain | leave blank until you have a real domain |

On the **Scopes** step you may add scopes now or leave it — the app requests what it needs at
runtime. Adding them here is required later for verification.

On the **Test users** step, **add your own email address**. While publishing status is
"Testing", only listed accounts can sign in, capped at 100. Skip this and your first sign-in
fails with `access_denied`, which looks exactly like a broken app.

**Step 3 — Create the client.**

1. **APIs & Services → Credentials → Create credentials → OAuth client ID**
2. **Application type: Web application** ← the important choice
3. Name: `studentai-backend`
4. Under **Authorised redirect URIs**, click **Add URI** and enter *exactly*:

   ```
   http://localhost:8000/api/auth/callback
   ```

   Google matches this as a literal string. A trailing slash, `127.0.0.1` instead of
   `localhost`, or `https` instead of `http` are all different URIs and all produce
   `redirect_uri_mismatch`.

   When you deploy, come back and add the production callback as a second entry —
   `https://your-backend.up.railway.app/api/auth/callback`. You can have several.

5. **Create.** Google shows the Client ID and Client Secret once.
6. Update **both** lines in `.env` this time:

   ```
   GOOGLE_CLIENT_ID=<new id>.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=<new secret>
   ```

Copy them straight from the browser into your editor. Do not route them through a screenshot,
a chat message, or a scratch file.

---

### 4d. Verify

```bash
cd "/home/arjun/Desktop/Student Personal Ai Assistant/student-ai-assistant/backend"
./venv/bin/python -c "
from app.config import settings
cid = settings.google_client_id
print('client id     :', cid[:20] + '…' if cid else 'MISSING')
print('project number:', cid.split('-')[0] if cid else '?')
print('secret        :', 'set (' + str(len(settings.google_client_secret)) + ' chars)' if settings.google_client_secret else 'MISSING')
print('redirect uri  :', settings.google_redirect_uri)
"
```

Check that the project number matches the project you just worked in, and that the redirect URI
is character-for-character what you registered.

The real test is a working sign-in, once you have a database (see the end of this document).

- [ ] Right project identified, client is **Web application** type
- [ ] The three APIs enabled, consent screen configured, my email in Test users
- [ ] Redirect URI registered exactly
- [ ] `.env` updated, old secret deleted

---

### If sign-in fails later, it is almost always one of these

| Error | Cause |
|---|---|
| `redirect_uri_mismatch` | Registered URI differs from `GOOGLE_REDIRECT_URI`, often by a slash |
| `access_denied` | Your email is not in **Test users** while status is "Testing" |
| `API has not been used in project…` | The Classroom / Calendar / Gmail API is not enabled |
| `invalid_client` | Client ID and secret are from different clients or projects |
| No refresh token stored | `prompt=consent` missing — already set in `app/api/auth.py` |

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

## Next: the database

You already have one running locally (`scripts/dev_db.py`), so nothing here blocks you.
For deployment you will want Supabase.

Full guide, including which Supabase connection mode to pick and why the free tier
pauses: **[`docs/DATABASE.md`](DATABASE.md)**
