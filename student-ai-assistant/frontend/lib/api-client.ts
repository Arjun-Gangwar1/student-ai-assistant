/**
 * Backend API client.
 *
 * Every request carries the session cookie and no endpoint takes a student id:
 * identity comes from the session server-side. The previous client passed
 * `studentId` into URLs and request bodies, which is what let any caller read
 * another student's data.
 */

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfter?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** The session expired or was never established. */
  get isUnauthenticated() {
    return this.status === 401;
  }

  get isRateLimited() {
    return this.status === 429;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      credentials: "include",
      headers: { "Content-Type": "application/json", ...init?.headers },
      ...init,
    });
  } catch {
    // fetch() rejects only on network failure — the backend being down looks
    // identical to being offline, so say something true for both.
    throw new ApiError("Can't reach the server. Check your connection.", 0);
  }

  if (res.status === 204) return undefined as T;

  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as { detail?: string });
    const retryAfter = Number(res.headers.get("Retry-After")) || undefined;
    throw new ApiError(body.detail ?? `Request failed (${res.status})`, res.status, retryAfter);
  }

  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  apiFetch<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
const put = <T>(path: string, body: unknown) =>
  apiFetch<T>(path, { method: "PUT", body: JSON.stringify(body) });
const patch = <T>(path: string, body?: unknown) =>
  apiFetch<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined });
const del = <T>(path: string) => apiFetch<T>(path, { method: "DELETE" });

// ─── Types ───────────────────────────────────────────────────────────────────

export interface Me {
  student_id: string;
  email: string;
  name: string | null;
  year: number | null;
  branch: string | null;
  gmail_enabled: boolean;
  telegram_linked: boolean;
}

export interface StudentProfile {
  id: string;
  email: string;
  name: string | null;
  year: number | null;
  branch: string | null;
  gmail_enabled: boolean;
  digest_time: string;
  telegram_linked: boolean;
}

export interface Deadline {
  id: string;
  title: string;
  due_at: string;
  source: string;
  confirmed: boolean;
  confidence: number;
  days_left: number;
  hours_left: number;
  priority_label: "HIGH" | "MEDIUM" | "LOW";
  /** AI-extracted below the confidence bar — must be shown as needing review. */
  needs_review: boolean;
}

export interface Item {
  id: string;
  title: string;
  summary: string | null;
  source: string;
  category: string | null;
  priority: "HIGH" | "MEDIUM" | "LOW";
  relevance_score: number;
  confidence: number;
  deadline: string | null;
  is_read: boolean;
  is_actioned: boolean;
  created_at: string;
  metadata?: Record<string, unknown>;
}

export interface EmailSummary {
  id: string;
  subject: string;
  sender_name: string;
  sender_email: string;
  received_at: string;
  snippet: string;
  has_attachments: boolean;
  attachment_count?: number;
  is_read: boolean;
}

export interface EmailAttachment {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  attachment_type: "file" | "link" | "image";
  url: string | null;
  extracted_text: string;
}

export interface EmailDetail extends EmailSummary {
  body_text: string;
  labels: string[];
  recipients: string[];
  attachments: EmailAttachment[];
}

export interface Source {
  id: string;
  title: string;
  source: string;
  created_at: string | null;
  deadline: string | null;
}

export interface ChatAnswer {
  answer: string;
  sources: Source[];
  remaining_today: number;
}

export interface SyncStatus {
  google_connected: boolean;
  has_refresh_token: boolean;
  connected_sources: { classroom: boolean; calendar: boolean; gmail: boolean };
  unprocessed_items: number;
  email_count: number;
  syncs_remaining_this_hour: number;
}

// ─── Auth & profile ──────────────────────────────────────────────────────────

export const loginUrl = () => `${BASE}/api/auth/login`;

export const getMe = () => apiFetch<Me>("/api/auth/me");
export const logout = () => post("/api/auth/logout");

export const getProfile = () => apiFetch<StudentProfile>("/api/auth/profile");
export const updateProfile = (data: { year?: number | null; branch?: string | null }) =>
  put<StudentProfile>("/api/auth/profile", data);
export const updateDigestTime = (digest_time: string) =>
  put<{ digest_time: string }>("/api/auth/digest-time", { digest_time });

export const setGmailEnabled = (enabled: boolean) =>
  put<{ gmail_enabled: boolean }>("/api/auth/gmail", { enabled });

export const createTelegramLink = () =>
  post<{ token: string; deep_link: string; instructions: string }>(
    "/api/auth/telegram/link-token",
  );
export const unlinkTelegram = () => del("/api/auth/telegram/link");

/** Data-portability export (DPDP Act). Opens as a file download. */
export const exportDataUrl = () => `${BASE}/api/auth/export`;
export const deleteAccount = () =>
  del<{ status: string; content_deleted_after_days: number }>("/api/auth/account");

// ─── Deadlines ───────────────────────────────────────────────────────────────

export const getDeadlines = (days = 14) =>
  apiFetch<{ deadlines: Deadline[]; total: number }>(`/api/deadlines?days=${days}`);

export const confirmDeadline = (
  deadlineId: string,
  body: { confirmed: boolean; corrected_due_at?: string },
) => patch(`/api/deadlines/${deadlineId}/confirm`, body);

export const submitFeedback = (body: {
  item_id?: string;
  deadline_id?: string;
  was_correct: boolean;
  corrected_deadline?: string;
  notes?: string;
}) => post("/api/deadlines/feedback", body);

// ─── Items ───────────────────────────────────────────────────────────────────

export function getItems(
  opts: {
    priority?: string;
    category?: string;
    source?: string;
    unread_only?: boolean;
    limit?: number;
    offset?: number;
  } = {},
) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(opts)) {
    if (value !== undefined && value !== false) params.set(key, String(value));
  }
  const query = params.toString();
  return apiFetch<{ items: Item[]; count: number }>(`/api/items${query ? `?${query}` : ""}`);
}

export const getItem = (itemId: string) => apiFetch<Item>(`/api/items/${itemId}`);
export const markRead = (itemId: string) => patch(`/api/items/${itemId}/read`);

// ─── Emails ──────────────────────────────────────────────────────────────────

export function getEmails(
  opts: { limit?: number; offset?: number; date?: string; sender?: string; subject?: string } = {},
) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(opts)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const query = params.toString();
  return apiFetch<{ emails: EmailSummary[]; total: number; limit: number; offset: number }>(
    `/api/emails${query ? `?${query}` : ""}`,
  );
}

export const searchEmails = (q: string, limit = 10) =>
  apiFetch<{ emails: EmailSummary[]; query: string }>(
    `/api/emails/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  );

export const getEmail = (emailId: string) => apiFetch<EmailDetail>(`/api/emails/${emailId}`);

// ─── Chat ────────────────────────────────────────────────────────────────────

export const askQuestion = (
  question: string,
  history?: { role: "user" | "assistant"; content: string }[],
) => post<ChatAnswer>("/api/chat/ask", { question, history });

export const getChatQuota = () =>
  apiFetch<{ remaining_today: number; daily_limit: number }>("/api/chat/quota");

// ─── Sync ────────────────────────────────────────────────────────────────────

export const syncNow = () =>
  post<{ status: string; results: Record<string, unknown>; syncs_remaining_this_hour: number }>(
    "/api/sync/now",
  );

export const getSyncStatus = () => apiFetch<SyncStatus>("/api/sync/status");
