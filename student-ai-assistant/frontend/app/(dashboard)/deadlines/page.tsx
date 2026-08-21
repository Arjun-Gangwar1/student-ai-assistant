"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Check, X } from "lucide-react";
import {
  ApiError,
  confirmDeadline,
  getDeadlines,
  type Deadline,
} from "@/lib/api-client";

const PRIORITY_STYLE: Record<string, string> = {
  HIGH: "bg-red-500/10 border-red-500/30",
  MEDIUM: "bg-amber-500/10 border-amber-500/30",
  LOW: "bg-green-500/10 border-green-500/30",
};

const PRIORITY_DOT: Record<string, string> = {
  HIGH: "bg-red-500",
  MEDIUM: "bg-amber-500",
  LOW: "bg-green-500",
};

export default function DeadlinesPage() {
  const [deadlines, setDeadlines] = useState<Deadline[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    getDeadlines(30)
      .then((res) => setDeadlines(res.deadlines))
      .catch((err) => {
        if (err instanceof ApiError && err.isUnauthenticated) window.location.href = "/";
      })
      .finally(() => setLoading(false));
  }, []);

  async function handleReview(id: string, correct: boolean) {
    setBusy(id);
    try {
      await confirmDeadline(id, { confirmed: correct });
      setDeadlines((prev) =>
        correct
          ? prev.map((d) => (d.id === id ? { ...d, confirmed: true, needs_review: false } : d))
          // Marked wrong: the backend dismisses it, so drop it from the list too.
          : prev.filter((d) => d.id !== id),
      );
    } catch {
      // Leave the row in place — a failed correction must not look like it worked.
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <div className="space-y-3 animate-pulse" aria-busy>
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-24 bg-slate-800 rounded-xl" />
        ))}
      </div>
    );
  }

  const unconfirmed = deadlines.filter((d) => d.needs_review);
  const confirmed = deadlines.filter((d) => !d.needs_review);

  return (
    <div>
      <h1 className="text-xl font-bold mb-5 text-slate-100">Deadlines</h1>

      {deadlines.length === 0 && (
        <div className="text-slate-500 text-center py-16 bg-slate-800 rounded-xl">
          🎉 Nothing due in the next 30 days
        </div>
      )}

      {unconfirmed.length > 0 && (
        <section className="mb-8">
          <div className="flex items-center gap-2 mb-1">
            <AlertTriangle className="w-4 h-4 text-amber-400" />
            <h2 className="text-sm font-semibold text-amber-300">Needs your confirmation</h2>
          </div>
          <p className="text-xs text-slate-500 mb-3">
            These were read out of notices or email by AI. Reminders stay off until
            you confirm them.
          </p>
          <div className="space-y-3">
            {unconfirmed.map((d) => (
              <DeadlineCard
                key={d.id}
                deadline={d}
                busy={busy === d.id}
                onReview={handleReview}
              />
            ))}
          </div>
        </section>
      )}

      {confirmed.length > 0 && (
        <section>
          {unconfirmed.length > 0 && (
            <h2 className="text-sm font-semibold text-slate-400 mb-3">Confirmed</h2>
          )}
          <div className="space-y-3">
            {confirmed.map((d) => (
              <DeadlineCard key={d.id} deadline={d} busy={false} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function DeadlineCard({
  deadline,
  busy,
  onReview,
}: {
  deadline: Deadline;
  busy: boolean;
  onReview?: (id: string, correct: boolean) => void;
}) {
  const { id, title, source, due_at, days_left, hours_left, priority_label, needs_review } =
    deadline;

  return (
    <div className={`border rounded-xl p-4 ${PRIORITY_STYLE[priority_label] ?? PRIORITY_STYLE.LOW}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2 min-w-0">
          <span
            className={`w-2 h-2 rounded-full shrink-0 mt-1.5 ${PRIORITY_DOT[priority_label]}`}
          />
          <div className="min-w-0">
            <p className="font-medium text-slate-100 leading-snug">{title}</p>
            <p className="text-xs text-slate-500 mt-0.5 capitalize">{source}</p>
          </div>
        </div>

        <div className="text-right shrink-0">
          <p className="text-sm font-semibold text-slate-200">
            {days_left < 0
              ? "Overdue"
              : days_left === 0
                ? `${hours_left}h left`
                : days_left === 1
                  ? "Tomorrow"
                  : `${days_left} days`}
          </p>
          <p className="text-xs text-slate-500 mt-0.5">
            {new Date(due_at).toLocaleString("en-IN", {
              day: "numeric",
              month: "short",
              hour: "2-digit",
              minute: "2-digit",
              timeZone: "Asia/Kolkata",
            })}
          </p>
        </div>
      </div>

      {needs_review && onReview && (
        <div className="mt-3 pt-3 border-t border-slate-700/50 flex items-center gap-2">
          <span className="text-xs text-slate-400 flex-1">Is this right?</span>
          <button
            onClick={() => onReview(id, true)}
            disabled={busy}
            className="flex items-center gap-1 px-2.5 py-1 bg-green-600/20 hover:bg-green-600/40 disabled:opacity-50 text-green-300 rounded-lg text-xs transition-colors"
          >
            <Check className="w-3 h-3" /> Yes
          </button>
          <button
            onClick={() => onReview(id, false)}
            disabled={busy}
            className="flex items-center gap-1 px-2.5 py-1 bg-red-600/20 hover:bg-red-600/40 disabled:opacity-50 text-red-300 rounded-lg text-xs transition-colors"
          >
            <X className="w-3 h-3" /> No
          </button>
        </div>
      )}
    </div>
  );
}
