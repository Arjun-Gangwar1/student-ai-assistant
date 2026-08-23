"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { RefreshCw } from "lucide-react";
import {
  ApiError,
  getDeadlines,
  getItems,
  getMe,
  getSyncStatus,
  syncNow,
  type Deadline,
  type Item,
  type SyncStatus,
} from "@/lib/api-client";
import DeadlineRadar from "@/components/DeadlineRadar";
import PriorityInbox from "@/components/PriorityInbox";

export default function Dashboard() {
  const [deadlines, setDeadlines] = useState<Deadline[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [name, setName] = useState("");
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const me = await getMe();
      setName(me.name ?? "");
      // No student id in these calls — the session identifies the caller.
      const [deadlineRes, itemRes, syncStatus] = await Promise.all([
        getDeadlines(7),
        getItems({ priority: "HIGH", limit: 5, unread_only: true }),
        // Lets the empty state distinguish "nothing due" from "never synced" —
        // an empty radar otherwise reads as a broken or failed sign-in.
        getSyncStatus().catch(() => null),
      ]);
      setDeadlines(deadlineRes.deadlines);
      setItems(itemRes.items);
      setStatus(syncStatus);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.isUnauthenticated) {
        window.location.href = "/";
        return;
      }
      setError(err instanceof Error ? err.message : "Could not load your dashboard");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleSync() {
    setSyncing(true);
    setError(null);
    try {
      await syncNow();
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError && err.isRateLimited
          ? err.message
          : "Sync failed. Please try again shortly.",
      );
    } finally {
      setSyncing(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-4 animate-pulse" aria-busy>
        <div className="h-4 bg-slate-800 rounded w-32" />
        <div className="grid grid-cols-3 gap-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-20 bg-slate-800 rounded-xl" />
          ))}
        </div>
        <div className="h-40 bg-slate-800 rounded-xl" />
      </div>
    );
  }

  const dueToday = deadlines.filter((d) => d.days_left === 0).length;
  const needsReview = deadlines.filter((d) => d.needs_review).length;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        {name && (
          <p className="text-slate-400 text-sm">
            Hey, <span className="text-slate-200 font-medium">{name.split(" ")[0]}</span> 👋
          </p>
        )}
        <button
          onClick={handleSync}
          disabled={syncing}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-indigo-400 disabled:opacity-50 transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${syncing ? "animate-spin" : ""}`} />
          {syncing ? "Syncing…" : "Sync"}
        </button>
      </div>

      {error && (
        <div className="bg-red-950/40 border border-red-900 rounded-xl px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* First-run state. Signing in does not sync, so a new account lands on an
          empty dashboard that looks identical to a failed login. Say which it is. */}
      {!error && deadlines.length === 0 && items.length === 0 && status?.google_connected && (
        <div className="bg-indigo-950/30 border border-indigo-800/50 rounded-xl px-4 py-4">
          <p className="text-indigo-200 text-sm font-medium">
            ✅ Signed in as {name || "you"} — nothing imported yet
          </p>
          <p className="text-indigo-200/70 text-xs mt-1 leading-relaxed">
            Connected:{" "}
            {Object.entries(status.connected_sources)
              .filter(([, on]) => on)
              .map(([source]) => source)
              .join(", ") || "nothing yet"}
            . Tap <span className="text-indigo-300">Sync</span> above to pull your
            assignments, calendar and mail. The first sync takes a minute or two.
          </p>
          <button
            onClick={handleSync}
            disabled={syncing}
            className="mt-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
          >
            {syncing ? "Syncing…" : "Sync now"}
          </button>
        </div>
      )}

      <div className="grid grid-cols-3 gap-3">
        <StatCard label="Due today" value={dueToday} tone={dueToday > 0 ? "danger" : "muted"} />
        <StatCard label="This week" value={deadlines.length} tone="warning" />
        <StatCard label="Unread" value={items.length} tone="accent" />
      </div>

      {/* Surfacing unconfirmed extractions is the trust mechanism: the student
          checks the AI's work rather than discovering it was wrong too late. */}
      {needsReview > 0 && (
        <Link
          href="/deadlines"
          className="block bg-amber-950/30 border border-amber-800/50 rounded-xl px-4 py-3 hover:bg-amber-950/50 transition-colors"
        >
          <p className="text-amber-200 text-sm font-medium">
            {needsReview} deadline{needsReview > 1 ? "s" : ""} need{needsReview === 1 ? "s" : ""} your confirmation
          </p>
          <p className="text-amber-200/60 text-xs mt-0.5">
            AI found these — check they&apos;re right →
          </p>
        </Link>
      )}

      <section>
        <SectionHeader title="Upcoming deadlines" href="/deadlines" action="View all" />
        <DeadlineRadar deadlines={deadlines} />
      </section>

      <section>
        <SectionHeader title="Needs attention" href="/chat" action="Ask AI" />
        <PriorityInbox items={items} />
      </section>
    </div>
  );
}

function SectionHeader({ title, href, action }: { title: string; href: string; action: string }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <h2 className="font-semibold text-slate-200">{title}</h2>
      <Link href={href} className="text-xs text-indigo-400 hover:text-indigo-300">
        {action} →
      </Link>
    </div>
  );
}

const TONES = {
  danger: "text-red-400",
  warning: "text-amber-400",
  accent: "text-indigo-400",
  muted: "text-slate-400",
} as const;

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: keyof typeof TONES;
}) {
  return (
    <div className="bg-slate-800 rounded-xl p-4 text-center border border-slate-700">
      <div className={`text-3xl font-bold ${TONES[tone]}`}>{value}</div>
      <div className="text-xs text-slate-500 mt-1">{label}</div>
    </div>
  );
}
