# API Reference

Base URL: `http://localhost:8000` in development.

**Authentication.** Every endpoint below except `/health` and the OAuth entry
points requires a session cookie (`studentai_session`), established by completing
`GET /api/auth/login`. Send it with `credentials: "include"`.

No endpoint accepts a student id. Identity comes from the session alone — the
earlier `/api/deadlines/{student_id}` style let any caller name someone else.

**Errors** are `{"detail": "..."}` with a conventional status: `401`
unauthenticated, `404` not found or not yours, `422` invalid input, `429`
rate-limited (with `Retry-After`), `500` unexpected.


## Authentication, profile and data rights

### `DELETE /api/auth/account`

Erasure request.

OAuth tokens are destroyed immediately and synchronously — access must stop
now, not whenever a purge job next runs. Content is removed after a 7-day
grace period so an accidental deletion is recoverable.

### `GET /api/auth/callback`

Complete the OAuth flow and establish a session.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `code` | query | string/null | no |  |
| `state` | query | string/null | no |  |
| `error` | query | string/null | no |  |

### `PUT /api/auth/digest-time`

Update Digest Time

**Body**

| Field | Type | Required |
|---|---|---|
| `digest_time` | string | yes |

### `GET /api/auth/export`

Everything held about this student, as JSON (right to portability).

### `PUT /api/auth/gmail`

Turn Gmail ingestion on or off.

Withdrawing consent must be as easy as giving it (DPDP Act, s.6). Turning it
off here stops future syncs; deleting already-ingested mail is the separate
/account endpoint below.

**Body**

| Field | Type | Required |
|---|---|---|
| `enabled` | boolean | yes |

### `GET /api/auth/login`

Begin the OAuth flow.

### `POST /api/auth/logout`

Logout

### `GET /api/auth/me`

Me

### `GET /api/auth/profile`

Get Profile

### `PUT /api/auth/profile`

Update Profile

**Body**

| Field | Type | Required |
|---|---|---|
| `year` | integer/null | no |
| `branch` | string/null | no |

### `DELETE /api/auth/telegram/link`

Remove Telegram Link

### `POST /api/auth/telegram/link-token`

Issue a one-shot token, returned as a deep link.

The webhook previously read `students.telegram_link_token`, a column that
existed in no migration, so linking could never have succeeded.


## Q&A

### `POST /api/chat/ask`

Ask

**Body**

| Field | Type | Required |
|---|---|---|
| `question` | string | yes |
| `history` | array/null | no |

### `GET /api/chat/quota`

Quota


## Deadlines

### `GET /api/deadlines`

Upcoming deadlines for the signed-in student.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `days` | query | integer | no |  |

### `POST /api/deadlines/feedback`

Free-form correction feedback on any extraction.

**Body**

| Field | Type | Required |
|---|---|---|
| `item_id` | string/null | no |
| `deadline_id` | string/null | no |
| `was_correct` | boolean | yes |
| `corrected_deadline` | string/null | no |
| `corrected_category` | string/null | no |
| `notes` | string/null | no |

### `PATCH /api/deadlines/{deadline_id}/confirm`

Confirm, correct, or dismiss an extracted deadline.

Every correction is also recorded as feedback — this is the dataset that
makes per-college extraction better than a generic model, and it is the only
part of the product a large vendor is unlikely to replicate.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `deadline_id` | path | string | yes |  |

**Body**

| Field | Type | Required |
|---|---|---|
| `confirmed` | boolean | yes |
| `corrected_due_at` | string/null | no |


## Email

### `GET /api/emails`

List Emails

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `limit` | query | integer | no |  |
| `offset` | query | integer | no |  |
| `date` | query | string/null | no | Calendar date in IST: YYYY-MM-DD |
| `sender` | query | string/null | no | Sender name or address, partial match |
| `subject` | query | string/null | no | Keyword in the subject line |

### `GET /api/emails/search`

Full-text search.

Runs against the generated `search_vector` column with a single ranked
query, rather than the previous two ILIKE scans merged in Python — which
could not rank, and could not use an index.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `q` | query | string | yes |  |
| `limit` | query | integer | no |  |

### `GET /api/emails/{email_id}`

Get Email

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `email_id` | path | string | yes |  |


## Items (unified inbox)

### `GET /api/items`

Filtered inbox for the signed-in student.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `priority` | query | string/null | no |  |
| `category` | query | string/null | no |  |
| `source` | query | string/null | no |  |
| `unread_only` | query | boolean | no |  |
| `limit` | query | integer | no |  |
| `offset` | query | integer | no |  |

### `GET /api/items/{item_id}`

One item in full. Scoped by student, so a foreign id returns 404.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `item_id` | path | string | yes |  |

### `PATCH /api/items/{item_id}/read`

Mark as read. Previously unauthenticated — any caller could flip any row.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `item_id` | path | string | yes |  |


## Sync

### `POST /api/sync/now`

Pull Classroom, Calendar and (if enabled) Gmail, then run the pipeline.

### `GET /api/sync/status`

What is connected and what is still waiting to be processed.


## Telegram

### `POST /api/telegram/webhook`

Receive an update from Telegram.

Always returns 200 for anything that is authentic: a non-2xx makes Telegram
retry the same update repeatedly, so a bug in one message handler would turn
into an infinite redelivery loop.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `x-telegram-bot-api-secret-token` | header | string/null | no |  |


## Operations

### `GET /health`

Liveness plus a real database round trip — Railway polls this.

