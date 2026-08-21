const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

// ── Auth ──────────────────────────────────────────────────────────────────────
export function getMe() {
  return apiFetch<{ student_id: string; email: string; name: string }>("/api/auth/me");
}

export interface StudentProfile {
  id: string;
  email: string;
  name: string;
  year: number | null;
  branch: string | null;
}

export function getProfile() {
  return apiFetch<StudentProfile>("/api/auth/profile");
}

export function updateProfile(data: { year?: number | null; branch?: string | null }) {
  return apiFetch<StudentProfile>("/api/auth/profile", {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `API error ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ── Deadlines ─────────────────────────────────────────────────────────────────
export function getDeadlines(studentId: string, days = 14) {
  return apiFetch<{ deadlines: Deadline[]; total: number }>(
    `/api/deadlines/${studentId}?days=${days}`
  );
}

export function confirmDeadline(
  deadlineId: string,
  body: { confirmed: boolean; corrected_due_at?: string }
) {
  return apiFetch(`/api/deadlines/${deadlineId}/confirm`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

// ── Items ─────────────────────────────────────────────────────────────────────
export function getItems(
  studentId: string,
  opts: { priority?: string; unread_only?: boolean; limit?: number } = {}
) {
  const params = new URLSearchParams();
  if (opts.priority) params.set("priority", opts.priority);
  if (opts.unread_only) params.set("unread_only", "true");
  if (opts.limit) params.set("limit", String(opts.limit));
  return apiFetch<{ items: Item[]; total: number }>(
    `/api/items/${studentId}?${params.toString()}`
  );
}

export function markRead(itemId: string) {
  return apiFetch(`/api/items/${itemId}/read`, { method: "PATCH" });
}

// ── Chat ──────────────────────────────────────────────────────────────────────
export function askQuestion(
  studentId: string,
  question: string,
  history?: { role: string; content: string }[]
) {
  return apiFetch<{ answer: string; sources: Source[] }>("/api/chat/ask", {
    method: "POST",
    body: JSON.stringify({ question, student_id: studentId, history }),
  });
}

// ── Types ─────────────────────────────────────────────────────────────────────
export interface Deadline {
  id: string;
  title: string;
  due_at: string;
  source: string;
  confirmed: boolean;
  days_left: number;
  hours_left: number;
  priority_label: "HIGH" | "MEDIUM" | "LOW";
}

export interface Item {
  id: string;
  title: string;
  summary: string;
  source: string;
  category: string;
  priority: "HIGH" | "MEDIUM" | "LOW";
  relevance_score: number;
  deadline?: string;
  is_read: boolean;
  created_at: string;
}

export interface Source {
  id: string;
  title: string;
  source: string;
  created_at: string;
}
