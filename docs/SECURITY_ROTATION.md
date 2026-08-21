# 🔴 Credential Rotation Runbook

**Status: URGENT — do this before deploying anything.**

Five live production credentials were stored in `student-ai-assistant/backend/.env.example`,
a file that is normally committed to version control. They have been removed from that file,
but **removal is not rotation**. Treat every value below as compromised: it existed in
plaintext in a shareable, non-ignored file for over two months.

Tick each box as you go.

---

### [ ] 1. Supabase service-role key — *highest severity*

The `service_role` key bypasses every Row Level Security policy. It is full database admin.

The old project (`nfegtbxyesezcagiiyug`) no longer resolves and is presumed deleted, which
closes this one by accident rather than by design. When you create the replacement project:

- Never place the `service_role` key anywhere but `.env` (already gitignored).
- The frontend must only ever see the `anon` key — and this app's frontend needs neither,
  because it talks to the FastAPI backend, not to Supabase directly.

### [ ] 2. Google OAuth client secret

1. https://console.cloud.google.com → **APIs & Services → Credentials**
2. Open the OAuth 2.0 Client ID ending `...j67n82`
3. **Reset Secret** (or delete the client and create a new one)
4. Update `GOOGLE_CLIENT_SECRET` in `.env`

> Anyone holding this secret plus your client ID can run a consent screen that looks like
> yours. With `gmail.readonly` in the scope list, that is a phishing vector against your users.

### [ ] 3. Groq API key

Verified still live and billable as of 2026-08-21.

1. https://console.groq.com/keys
2. Delete the key beginning `gsk_WW8vOD...`
3. Create a new one, update `GROQ_API_KEY` in `.env`

### [ ] 4. Telegram bot token

Verified still live as of 2026-08-21 (`getMe` → HTTP 200). Whoever has this token controls
the bot completely: they can read every message students send it and send messages as you.

1. Telegram → **@BotFather** → `/mybots` → select the bot → **API Token** → **Revoke current token**
2. Update `TELEGRAM_BOT_TOKEN` in `.env`
3. Re-register the webhook after rotating (the backend does this on startup in production)

### [ ] 5. App `SECRET_KEY` (session signing)

This signs session cookies. With it, anyone can forge a session for any `student_id` and
read that student's data through the API.

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Rotating invalidates all existing sessions — users log in again. That is the intended effect.

### [ ] 6. `OPENAI_API_KEY`

An `OPENAI_API_KEY` was present in `.env.example`, though the codebase no longer uses OpenAI
(embeddings run locally via `sentence-transformers`). Revoke it at
https://platform.openai.com/api-keys and drop the variable — an unused key is pure liability.

### [ ] 7. New — `TOKEN_ENCRYPTION_KEY`

Newly introduced. Encrypts Google OAuth tokens at rest so a future database compromise does
not hand over your users' mailboxes.

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> **Do not lose or change this key once users exist** — every stored token becomes
> undecryptable and every user must re-authenticate. Back it up in a password manager.

---

## Why plaintext OAuth tokens made this worse

`students.google_tokens` stored Google **refresh tokens** as plaintext JSONB. A refresh
token does not expire on its own. Combined with the exposed `service_role` key, one leak
gave a reader persistent access to every connected student's Gmail, Classroom, and Calendar —
without the student ever seeing a login alert.

Migration `002` encrypts this column. See `app/utils/crypto.py`.

---

## Ongoing hygiene

- `.env.example` holds **placeholders only** — enforced by `scripts/check_secrets.py`
- `.gitignore` covers `.env` and `.env.*` with an explicit `!.env.example` exception
- Run `python scripts/check_secrets.py` before every push (or wire it as a pre-commit hook)
- When you add a real remote, **do not** force-push this history anywhere the pre-scrub
  version of `.env.example` might still exist
