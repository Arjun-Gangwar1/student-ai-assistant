"use client";

import { useEffect, useState } from "react";
import { getDeadlines, confirmDeadline, getMe, type Deadline } from "@/lib/api-client";
import { formatDistanceToNow } from "date-fns";

const PRIORITY_STYLE: Record<string, string> = {
  HIGH:   "bg-red-500/10 border-red-500/30 text-red-300",
  MEDIUM: "bg-amber-500/10 border-amber-500/30 text-amber-300",
  LOW:    "bg-green-500/10 border-green-500/30 text-green-300",
};

const PRIORITY_DOT: Record<string, string> = {
  HIGH: "bg-red-500", MEDIUM: "bg-amber-500", LOW: "bg-green-500",
};

export default function DeadlinesPage() {
  const [deadlines, setDeadlines] = useState<Deadline[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getMe()
      .then((me) => getDeadlines(me.student_id, 30))
      .then((r) => setDeadlines(r.deadlines))
      .catch(() => { window.location.href = "/"; })
      .finally(() => setLoading(false));
  }, []);

  const handleConfirm = async (id: string, confirmed: boolean) => {
    await confirmDeadline(id, { confirmed });
    setDeadlines((prev) =>
      prev.map((d) => (d.id === id ? { ...d, confirmed } : d))
    );
  };

  if (loading) {
    return <div className="text-slate-400 text-center py-16 animate-pulse">Loading deadlines...</div>;
  }

  return (
    <div>
      <h1 className="text-xl font-bold mb-5 text-slate-100">All Deadlines</h1>

      {deadlines.length === 0 && (
        <div className="text-slate-500 text-center py-16 bg-slate-800 rounded-xl">
          🎉 No upcoming deadlines in the next 14 days!
        </div>
      )}

      <div className="space-y-3">
        {deadlines.map((d) => (
          <div
            key={d.id}
            className={`border rounded-xl p-4 ${PRIORITY_STYLE[d.priority_label] ?? PRIORITY_STYLE.LOW}`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-2 min-w-0">
                <span className={`w-2 h-2 rounded-full flex-shrink-0 mt-1 ${PRIORITY_DOT[d.priority_label]}`} />
                <div className="min-w-0">
                  <p className="font-medium text-slate-100 truncate">{d.title}</p>
                  <p className="text-xs text-slate-400 mt-0.5 capitalize">{d.source}</p>
                </div>
              </div>
              <div className="text-right flex-shrink-0">
                <p className="text-sm font-semibold">
                  {d.days_left === 0
                    ? `${d.hours_left}h left`
                    : d.days_left === 1
                    ? "Tomorrow"
                    : `${d.days_left} days`}
                </p>
                <p className="text-xs text-slate-500 mt-0.5">
                  {new Date(d.due_at).toLocaleDateString("en-IN", {
                    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
                  })}
                </p>
              </div>
            </div>

            {/* Confirm/correct low-confidence deadlines */}
            {!d.confirmed && (
              <div className="mt-3 flex items-center gap-2 text-xs">
                <span className="text-slate-400">AI extracted this — is it correct?</span>
                <button
                  onClick={() => handleConfirm(d.id, true)}
                  className="px-2 py-1 bg-green-600/20 hover:bg-green-600/40 text-green-300 rounded"
                >
                  ✓ Yes
                </button>
                <button
                  onClick={() => handleConfirm(d.id, false)}
                  className="px-2 py-1 bg-red-600/20 hover:bg-red-600/40 text-red-300 rounded"
                >
                  ✗ Wrong
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
